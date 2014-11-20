import textwrap
import os.path

from dominator.entities import (Image, SourceImage, ConfigVolume, DataVolume, LogVolume, LogFile, Task,
                                Container, YamlFile, TemplateFile, TextFile, RotatedLogFile, Door, Url)
from dominator.utils import cached, resource_stream, resource_string
from obedient.zookeeper import build_zookeeper_cluster, filter_quorum_ships


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
        files={'/scripts/elasticsearch.sh': resource_stream('elasticsearch.sh')},
        command=['/scripts/elasticsearch.sh'],
    )


def create_elasticsearch(clustername):
    image = get_elasticsearch_image()
    data = DataVolume(image.volumes['data'])
    logs = LogVolume(
        image.volumes['logs'],
        files={
            '{}.log'.format(clustername): RotatedLogFile('[%Y-%m-%d %H:%M:%S,%f]', 25)
        },
    )

    config = ConfigVolume(
        dest=image.volumes['config'],
        files={
            'mapping.json': TextFile(resource_string('mapping.json')),
            'logging.yml': TextFile(resource_string('logging.yml')),
        },
    )

    container = Container(
        name='elasticsearch',
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
            'ES_JAVA_OPTS': '-XX:NewRatio=5',
            'ES_CLASSPATH': config.files['logging.yml'].fulldest,
        },
    )

    def create_elasticsearch_config(container=container):
        marvel_agent = {}
        if 'marvel' in container.links:
            marvel_agent['exporter.es.hosts'] = [link.hostport for link in container.links['marvel']]
        else:
            marvel_agent['enabled'] = False

        ships = [door.container.ship for door in container.links['elasticsearch']] + [container.ship]
        config = {
            'cluster.name': clustername,
            'node': {
                'name': container.ship.name,
                'datacenter': container.ship.datacenter,
            },
            'transport.tcp.port': container.doors['peer'].internalport,
            'transport.publish_port': container.doors['peer'].port,
            'http.port': container.doors['http'].internalport,
            'network.publish_host': container.ship.fqdn,
            'discovery': None,
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
        if 'zookeeper' in container.links:
            config['discovery'] = {'type': 'com.sonian.elasticsearch.zookeeper.discovery.ZooKeeperDiscoveryModule'}
            config['sonian.elasticsearch.zookeeper'] = {
                'settings.enabled': False,
                'client.host': ','.join([link.hostport for link in container.links['zookeeper']]),
                'discovery.state_publishing.enabled': True,
            }
            config['zookeeper.root'] = '/{}/elasticsearch'.format(clustername)
        else:
            config['discovery.zen'] = {
                'ping': {
                    'multicast.enabled': False,
                    'unicast.hosts': [door.hostport for door in container.links['elasticsearch']],
                },
                'minimum_master_nodes': (len(container.links['elasticsearch']) + 1) // 2 + 1,
            }

        return YamlFile(config)

    def create_env(container=container):
        arguments = [
            '-server',
            '-showversion',
        ]
        jmxport = container.doors['jmx'].internalport
        options = {
            '-Des.default.config': os.path.join(config.dest, 'elasticsearch.yml'),
            '-Des.default.path.home': '/usr/share/elasticsearch',
            '-Des.default.path.logs': logs.dest,
            '-Des.default.path.data': data.dest,
            '-Des.default.path.work': '/tmp/elasticsearch',
            '-Des.default.path.conf': config.dest,
            '-Dcom.sun.management.jmxremote.authenticate': False,
            '-Dcom.sun.management.jmxremote.ssl': False,
            '-Dcom.sun.management.jmxremote.local.only': False,
            '-Dcom.sun.management.jmxremote.port': jmxport,
            '-Dcom.sun.management.jmxremote.rmi.port': jmxport,
            '-Djava.rmi.server.hostname': container.ship.fqdn,
            '-Dvisualvm.display.name': container.fullname,
        }

        jvmflags = arguments + ['{}={}'.format(key, value) for key, value in options.items()]
        text = 'export JAVA_OPTS="{}"'.format(' '.join(sorted(jvmflags)))
        if container.memory > 0:
            text += '\nexport ES_HEAP_SIZE={}'.format(container.memory // 2)
        return TextFile(text)

    config.files['elasticsearch.yml'] = create_elasticsearch_config
    config.files['env.sh'] = create_env
    return container


def clusterize_elasticsearches(elasticsearches):
    """Link each container with all other containers."""
    for me in elasticsearches:
        me.links['elasticsearch'] = [sibling.doors['peer'] for sibling in elasticsearches if sibling != me]


@cached
def get_kibana_image():
    httpport = 81
    parent = get_nginx_image()
    return SourceImage(
        name='kibana',
        parent=parent,
        scripts=[
            'curl -s https://download.elasticsearch.org/kibana/kibana/kibana-3.1.1.tar.gz | tar -zxf -',
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


def create_kibana():
    image = get_kibana_image()
    return Container(
        name='kibana',
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
        files={'/etc/nginx/nginx.conf': resource_stream('nginx.conf')},
        ports={'http': 80},
        volumes={
            'logs': '/var/log/nginx',
            'sites': '/etc/nginx/sites-enabled',
            'ssl': '/etc/nginx/certs',
        },
        command=['nginx'],
    )


def create_nginx_front(elasticsearch, kibana):
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

    def copyurls(door):
        return {name: Url(url.path) for name, url in door.urls.items()}

    return Container(
        name='nginx',
        image=image,
        volumes={
            'sites': sites,
            'logs': logs,
            'ssl': ssl,
        },
        doors={
            'kibana.http': Door(schema='http', port=image.ports['http'], urls=copyurls(kibana.doors['http'])),
            'kibana.https': Door(schema='https', port=443, urls=copyurls(kibana.doors['http'])),
            'elasticsearch.http': Door(schema='http', port=9200, urls=copyurls(elasticsearch.doors['http'])),
            'elasticsearch.https': Door(schema='https', port=9443, urls=copyurls(elasticsearch.doors['http'])),
        },
        links={
            'kibana': kibana.doors['http'].urls['default'],
            'elasticsearch': elasticsearch.doors['http'].urls['default'],
        },
        memory=1024**2*256,
    )


def create_dump_task(elasticsearch):
    dump = Task(
        name='dump',
        image=SourceImage(
            name='elasticdump',
            parent=Image(namespace='yandex', repository='trusty'),
            scripts=[
                'apt-get update && apt-get install -y npm',
                'ln -s nodejs /usr/bin/node',
                'npm install elasticdump -g',
            ],
            files={
                '/scripts/dump.sh': textwrap.dedent('''
                    . /scripts/config/dump.env
                    INDEX=$1
                    if [ -z "$INDEX"  ]; then
                        echo "Usage: dump <INDEX>"
                        exit 1
                    fi
                    elasticdump --input=$URL/$INDEX --output=$
                '''),
            },
            command=['/scripts/dump.sh'],
            entrypoint=['bash'],
        ),
        volumes={
            'config': ConfigVolume(
                dest='/scripts/config',
                files={
                    'dump.env': TextFile(text='URL={}'.format(elasticsearch.doors['http'].urls['default'])),
                },
            ),
        },
    )
    return dump


def build_elasticsearch_cluster(ships, clustername):
    elasticsearches = []
    for ship in ships:
        elasticsearch = create_elasticsearch(clustername=clustername)
        elasticsearches.append(elasticsearch)
        yield elasticsearch
        ship.place(elasticsearch)
    clusterize_elasticsearches(elasticsearches)


def attach_kibana_to_elasticsearch(elasticsearches):
    for elasticsearch in elasticsearches:
        kibana = create_kibana()
        nginx = create_nginx_front(elasticsearch, kibana)

        for doorname in ['elasticsearch.http', 'elasticsearch.https']:
            kibana.links[doorname] = nginx.doors[doorname]

        yield nginx, kibana

        elasticsearch.ship.place(kibana)
        elasticsearch.ship.place(nginx)


def attach_zookeeper_to_elasticsearch(elasticsearches, zookeepers):
    zkdoors = [zookeeper.doors['client'] for zookeeper in zookeepers]
    for elasticsearch in elasticsearches:
        elasticsearch.links['zookeeper'] = zkdoors


def test(shipment):
    shipment.unload_ships()
    ships = shipment.ships.values()
    zookeepers = build_zookeeper_cluster(filter_quorum_ships(ships))
    elasticsearches = list(build_elasticsearch_cluster(ships, 'testcluster'))
    attach_zookeeper_to_elasticsearch(elasticsearches, zookeepers)
    list(attach_kibana_to_elasticsearch(elasticsearches))

    # Adjust memory to prevent OOM when running on the laptop
    for elasticsearch in elasticsearches:
        elasticsearch.memory = min(elasticsearch.ship.memory, 128*1024*1024)

    shipment.expose_ports(list(range(51000, 51100)))
