import textwrap
import copy

from dominator.entities import (LocalShip, Image, SourceImage, ConfigVolume, DataVolume, LogVolume, LogFile,
                                Container, YamlFile, TemplateFile, TextFile, RotatedLogFile, Shipment, Door, Url)
from dominator.utils import cached, aslist, groupbysorted, resource_string
from obedient.zookeeper import create as create_zookeeper


def filter_zookeeper_ships(ships):
    """Return odd number of ships, one(two) from each datacenter."""
    zooships = [list(dcships)[0] for datacenter, dcships in groupbysorted(ships, lambda s: s.datacenter)]
    if len(zooships) % 2 == 0:
        # If we have even datacenter count, then add one more ship for quorum
        zooships.append([ship for ship in ships if ship not in zooships][0])
    return zooships


@cached
def get_elasticsearch_image():
    return SourceImage(
        name='elasticsearch',
        parent=Image(namespace='yandex', repository='trusty'),
        scripts=[
            'curl http://packages.elasticsearch.org/GPG-KEY-elasticsearch | apt-key add -',
            'echo "deb http://packages.elasticsearch.org/elasticsearch/1.3/debian stable main"'
            ' > /etc/apt/sources.list.d/elasticsearch.list',
            'apt-get update',
            'apt-get install -y --no-install-recommends maven elasticsearch=1.3.2 openjdk-7-jdk',
            'git clone https://github.com/grmblfrz/elasticsearch-zookeeper.git /tmp/elasticsearch-zookeeper',
            'cd /tmp/elasticsearch-zookeeper && git checkout v1.3.1 && '
            'mvn package -Dmaven.test.skip=true -Dzookeeper.version=3.4.6',
            '/usr/share/elasticsearch/bin/plugin -v '
            '  -u file:///tmp/elasticsearch-zookeeper/target/releases/elasticsearch-zookeeper-1.3.1.zip '
            '  -i elasticsearch-zookeeper-1.3.1',
            '/usr/share/elasticsearch/bin/plugin -v -i elasticsearch/marvel/latest',
            '/usr/share/elasticsearch/bin/plugin -v -i mobz/elasticsearch-head',
        ],
        ports={'http': 9201, 'peer': 9301, 'jmx': 9401},
        volumes={
            'logs': '/var/log/elasticsearch',
            'data': '/var/lib/elasticsearch',
            'config': '/etc/elasticsearch'
        },
        files={'/scripts/elasticsearch.sh': resource_string('elasticsearch.sh')},
        command='bash /scripts/elasticsearch.sh',
    )


@aslist
def create_elasticsearch(ships, name):
    image = get_elasticsearch_image()
    data = DataVolume(image.volumes['data'])
    logs = LogVolume(
        image.volumes['logs'],
        files={
            '{}.log'.format(name): RotatedLogFile('[%Y-%m-%d %H:%M:%S,%f]', 25)
        },
    )

    def create_elasticsearch_config(container):
        marvel_agent = {}
        if 'marvel' in container.links:
            marvel_agent['exporter.es.hosts'] = [link.hostport for link in container.links['marvel']]
        else:
            marvel_agent['enabled'] = False

        return {
            'cluster.name': name,
            'node': {
                'name': container.ship.name,
                'datacenter': container.ship.datacenter,
            },
            'transport.tcp.port': container.doors['peer'].port,
            'transport.publish_port': container.doors['peer'].externalport,
            'http.port': container.doors['http'].port,
            'network.publish_host': container.ship.fqdn,
            'discovery': {
                'type': 'com.sonian.elasticsearch.zookeeper.discovery.ZooKeeperDiscoveryModule',
            },
            'sonian.elasticsearch.zookeeper': {
                'settings.enabled': False,
                'client.host': ','.join([link.hostport for link in container.links['zookeeper']]),
                'discovery.state_publishing.enabled': True,
            },
            'zookeeper.root': '/{}/elasticsearch'.format(name),
            'cluster.routing.allocation': {
                'awareness': {
                    'force.datacenter.values': sorted({ship.datacenter for ship in ships}),
                    'attributes': 'datacenter',
                },
                'cluster_concurrent_rebalance': 10,
                'disk.threshold_enabled': True,
                'node_initial_primaries_recoveries': 10,
                'node_concurrent_recoveries': 10,
            },
            'index': {
                'number_of_shards': 5,
                'number_of_replicas': 2,
                'mapper.default_mapping_location': container.volumes['config'].files['mapping.json'].fulldest,
                'query.default_field': 'msg',
                'store.type': 'mmapfs',
                'translog.flush_threshold_ops': 50000,
                'refresh_interval': '10s',
            },
            'indices': {
                'recovery.concurrent_streams': 20,
                'memory.index_buffer_size': '30%',
            },
            'marvel.agent': marvel_agent,
        }

    for ship in ships:
        config = ConfigVolume(
            dest=image.volumes['config'],
            files={
                'mapping.json': TextFile(filename='mapping.json'),
                'logging.yml': TextFile(filename='logging.yml'),
            },
        )

        container = Container(
            name='elasticsearch',
            ship=ship,
            image=image,
            volumes={
                'data': data,
                'logs': logs,
                'config': config,
            },
            doors={
                'http': Door(
                    schema='http',
                    port=image.ports['http'],
                    urls={
                        'head': Url('_plugin/head/'),
                        'marvel': Url('_plugin/marvel/'),
                    },
                ),
                'peer': Door(schema='elasticsearch-peer', port=image.ports['peer']),
                'jmx': Door(schema='rmi', port=image.ports['jmx']),
            },
            env={
                'JAVA_RMI_PORT': image.ports['jmx'],
                'JAVA_RMI_SERVER_HOSTNAME': ship.fqdn,
                'ES_HEAP_SIZE': ship.memory // 2,
                'ES_JAVA_OPTS': '-XX:NewRatio=5',
            },
            memory=ship.memory * 3 // 4,
        )

        # Wrap function to avoid "late binding"
        # Another workaround is to move create_elasticsearch_config definition here
        def create_config(container=container):
            return lambda: create_elasticsearch_config(container)

        config.files['elasticsearch.yml'] = YamlFile(create_config())
        yield container


@cached
def get_kibana_image():
    httpport = 81
    parent = get_nginx_image()
    return SourceImage(
        name='kibana',
        parent=parent,
        scripts=[
            'curl -s https://download.elasticsearch.org/kibana/kibana/kibana-3.1.0.tar.gz | tar -zxf -',
            'mkdir /var/www',
            'mv kibana-* /var/www/kibana',
            'ln -fs config/config.js /var/www/kibana/config.js',
        ],
        files={
            '/etc/nginx/sites-enabled/kibana.site': textwrap.dedent('''
                server {
                  listen [::]:%d ipv6only=off;
                  location / {
                    alias /var/www/kibana/;
                  }
                }''' % httpport)
        },
        volumes={
            'config': '/var/www/kibana/config',
        },
        ports={'http': httpport},
    )


def create_kibana(ship):
    image = get_kibana_image()
    return Container(
        name='kibana',
        ship=ship,
        image=image,
        volumes={
            'config': ConfigVolume(
                dest=image.volumes['config'],
                files={'config.js':  TemplateFile(resource_string('config.js'))},
            ),
            'logs': LogVolume(
                dest=image.parent.volumes['logs'],
                files={
                    'access.log': LogFile(),
                    'error.log': LogFile(),
                },
            ),
        },
        doors={
            'http': Door(schema='http', port=image.ports['http']),
        },
    )


@cached
def get_nginx_image():
    return SourceImage(
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


def create_nginx(ship, kibana, elasticsearch):
    image = get_nginx_image()
    sites = ConfigVolume(
        dest=image.volumes['sites'],
        files={'elk.site': TemplateFile(resource_string('elk.site'))},
    )

    logs = LogVolume(
        dest=image.volumes['logs'],
        files={
            'access.log': LogFile(),
            'error.log': LogFile(),
        },
    )

    ssl = ConfigVolume(
        dest=image.volumes['ssl'],
        files={'server.pem': TemplateFile('${this.ship.certificate}')},
    )

    return Container(
        name='nginx',
        image=image,
        ship=ship,
        volumes={
            'sites': sites,
            'logs': logs,
            'ssl': ssl,
        },
        doors={
            'kibana.http': Door(schema='http', port=image.ports['http'], urls=copy.copy(kibana.doors['http'].urls)),
            'kibana.https': Door(schema='https', port=443, urls=copy.copy(kibana.doors['http'].urls)),
            'elasticsearch.http': Door(schema='http', port=9200, urls=copy.copy(elasticsearch.doors['http'].urls)),
            'elasticsearch.https': Door(schema='https', port=9443, urls=copy.copy(elasticsearch.doors['http'].urls)),
        },
        links={
            'kibana': kibana.doors['http'].urls['default'],
            'elasticsearch': elasticsearch.doors['http'].urls['default'],
        },
        memory=1024**2*256,
    )


def create_elk(name, ships, port_offset=0):
    zookeepers = create_zookeeper(filter_zookeeper_ships(ships))
    elasticsearches = create_elasticsearch(ships, name)
    kibanas = [create_kibana(ship) for ship in ships]
    nginxes = [create_nginx(ship, kibana, elasticsearch)
               for ship, kibana, elasticsearch in zip(ships, kibanas, elasticsearches)]

    for nginx, kibana, elasticsearch in zip(nginxes, kibanas, elasticsearches):
        elasticsearch.links['zookeeper'] = [zookeeper.doors['client'].urls['default'] for zookeeper in zookeepers]
        kibana.links['elasticsearch.http'] = nginx.doors['elasticsearch.http'].urls['default']
        kibana.links['elasticsearch.https'] = nginx.doors['elasticsearch.https'].urls['default']

    containers = zookeepers + elasticsearches + kibanas + nginxes

    # Expose all ports
    for container in containers:
        for door in container.doors.values():
            door.externalport = door.port + port_offset

    return containers


def make_local(port_offset=50000):
    return Shipment('elk-local', create_elk(ships=[LocalShip()], name='elk-local', port_offset=port_offset))
