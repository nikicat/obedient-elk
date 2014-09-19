from dominator.entities import (LocalShip, Image, SourceImage, ConfigVolume, DataVolume,
                                Container, TemplateFile, Shipment, Door)
from dominator.utils import aslist, groupbysorted, resource_string
from obedient import elasticsearch
from obedient import zookeeper


def zookeeper_ships(ships):
    zooships = [list(dcships)[0] for datacenter, dcships in groupbysorted(ships, lambda s: s.datacenter)]
    if len(zooships) % 2 == 0:
        # If we have even datacenter count, then add one more ship for quorum
        zooships.append([ship for ship in ships if ship not in zooships][0])
    return zooships


@aslist
def create(
    ships,
    cluster_name,
    marvel_hosts=[],
    zookeepers=[],
    ports=None,
):
    ports = ports or {}

    if len(zookeepers) == 0:
        zookeepers = zookeeper.create(zookeeper_ships(ships))
        yield from zookeepers

    elasticsearchs = elasticsearch.create(ships, zookeepers, cluster_name,
                                          ports={'http': 9201, 'https': None}, marvel_hosts=marvel_hosts)
    yield from elasticsearchs

    nginx_image = SourceImage(
        name='nginx',
        parent=Image(namespace='yandex', repository='trusty'),
        env={'DEBIAN_FRONTEND': 'noninteractive'},
        scripts=[
            'apt-add-repository -y ppa:nginx/stable',
            'apt-get update',
            'apt-get install -yy nginx-extras',
            'rm -f /etc/nginx/sites-enabled/default',
            'wget https://gist.githubusercontent.com/rrx/6217900/raw/'
            '78c2a4817dad9611ab602834d56d0f5b00bb3cc9/gencert.sh',
            'bash gencert.sh localhost || true',
            'cat localhost.crt localhost.key > /etc/ssl/private/server.pem',
        ],
        files={'/etc/nginx/nginx.conf': resource_string('nginx.conf')},
        ports={'http': 80},
        volumes={
            'logs': '/var/log/nginx',
            'sites': '/etc/nginx/sites-enabled',
            'ssl': '/etc/nginx/certs',
        },
        command='nginx',
    )

    kibana_image = SourceImage(
        name='kibana',
        parent=nginx_image,
        scripts=[
            'curl -s https://download.elasticsearch.org/kibana/kibana/kibana-3.1.0.tar.gz | tar -zxf -',
            'mkdir /var/www',
            'mv kibana-* /var/www/kibana',
            'ln -fs config/config.js /var/www/kibana/config.js',
        ],
        volumes={'config': '/var/www/kibana/config'},
        ports={'http': 80},
    )
    kibana_image.files['/etc/nginx/sites-enabled/kibana.site'] = '''
server {{
  listen [::]:{} ipv6only=off;
  location / {{
    alias /var/www/kibana/;
  }}
}}'''.format(kibana_image.ports['http'])

    logs = DataVolume(nginx_image.volumes['logs'])
    ssl = ConfigVolume(
        dest=nginx_image.volumes['ssl'],
        files={'server.pem': TemplateFile('${this.ship.certificate}')},
    )
    configjs = resource_string('config.js')
    elk_site = resource_string('elk.site')

    for es in elasticsearchs:

        kibana = Container(
            name='kibana',
            ship=es.ship,
            image=kibana_image,
            volumes={'config': ConfigVolume(dest=kibana_image.volumes['config'])},
            doors={
                'http': Door(schema='http', port=kibana_image.ports['http'], externalport=1080),
            },
        )
        yield kibana

        nginx = Container(
            name='nginx',
            image=nginx_image,
            ship=es.ship,
            volumes={
                'sites': ConfigVolume(nginx_image.volumes['sites']),
                'logs': logs,
                'ssl': ssl,
            },
            doors={
                'kibana.http': Door(schema='http', port=nginx_image.ports['http'],
                                    externalport=ports.get('kibana.http')),
                'kibana.https': Door(schema='https', port=443,
                                     externalport=ports.get('kibana.https')),
                'elasticsearch.http': Door(schema='http', port=9200,
                                           externalport=ports.get('elsaticsearch.http')),
                'elasticsearch.https': Door(schema='https', port=9443,
                                            externalport=ports.get('elasticsearch.https')),
            },
            memory=1024**2*256,
        )
        yield nginx

        kibana.volumes['config'].files['config.js'] = TemplateFile(configjs, nginx=nginx)
        nginx.volumes['sites'].files['elk.site'] = TemplateFile(elk_site, elasticsearch=es, kibana=kibana)


def make_local():
    return Shipment('local', create([LocalShip()], 'local', ports={'kibana.http': 8080, 'kibana.https': 4433}))
