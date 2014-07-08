import io

from dominator.entities import (LocalShip, Image, SourceImage, ConfigVolume, DataVolume,
                                Container, TextFile, TemplateFile)
from dominator.utils import aslist, groupby
from obedient import elasticsearch
from obedient import zookeeper


def zookeeper_ships(ships):
    for datacenter, ships in groupby(ships, lambda s: s.datacenter):
        yield list(ships)[0]


@aslist
def create(ships, name, httpport=80, httpsport=443, marvel_hosts=[]):
    zookeepers = zookeeper.create(zookeeper_ships(ships))
    yield from zookeepers

    elasticsearchs = elasticsearch.create(ships, zookeepers, name, httpport=9201, marvel_hosts=marvel_hosts)
    yield from elasticsearchs

    nginx_image = SourceImage(
        name='nginx',
        parent=Image('yandex/trusty'),
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
        files={'/etc/nginx/nginx.conf': 'nginx.conf'},
        ports={'http': '80'},
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
            'curl https://download.elasticsearch.org/kibana/kibana/kibana-3.1.0.tar.gz | tar -zxf -',
            'mkdir /var/www',
            'mv kibana-* /var/www/kibana',
            'ln -fs config/config.js /var/www/kibana/config.js',
        ],
        volumes={'config': '/var/www/kibana/config'},
        ports={'http': 80},
    )
    kibana_image.files['/etc/nginx/sites-enabled/kibana.site'] = io.BytesIO(
        'server {{'
        '  listen [::]:{} ipv6only=off;'
        '  location / {{'
        '    alias /var/www/kibana/;'
        '  }}'
        '}}'.format(kibana_image.ports['http']).encode())

    logs = DataVolume(nginx_image.volumes['logs'])
    ssl = ConfigVolume(
        dest=nginx_image.volumes['ssl'],
        files={'server.pem': TemplateFile(TextFile(text='${this.ship.certificate}'))},
    )
    configjs = TextFile('config.js')

    for es in elasticsearchs:

        kibana = Container(
            name='kibana',
            ship=es.ship,
            image=kibana_image,
            volumes={'config': ConfigVolume(dest=kibana_image.volumes['config'])},
            ports={'http': 80},
            extports={'http': 1080}
        )
        yield kibana

        nginx = Container(
            name='nginx-elk',
            image=nginx_image,
            ship=es.ship,
            volumes={
                'sites': ConfigVolume(nginx_image.volumes['sites']),
                'logs': logs,
                'ssl': ssl,
            },
            ports={
                'kibana.http': 80,
                'kibana.https': 443,
                'elasticsearch.http': 9200,
                'elasticsearch.https': 9443,
            },
            extports={
                'kibana.http': httpport,
                'kibana.https': httpsport,
            },
            memory=1024**2*256,
        )
        yield nginx

        kibana.volumes['config'].files['config.js'] = TemplateFile(
            configjs,
            elasticsearch_ports=nginx.ports,
        )
        nginx.volumes['sites'].files['elk.site'] = TemplateFile(TextFile('elk.site'), elasticsearch=es, kibana=kibana)


def development():
    return create([LocalShip()], 'local', httpport=8080, httpsport=4433)
