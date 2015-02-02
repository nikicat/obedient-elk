import textwrap
import os.path
import mako.template

from dominator.entities import (Image, SourceImage, ConfigVolume, DataVolume, LogVolume, LogFile, Task,
                                Container, YamlFile, TextFile, RotatedLogFile, Door, Url)
from dominator.utils import cached, resource_stream, resource_string
from obedient.zookeeper import build_zookeeper_cluster, filter_quorum_ships


@cached
def get_elasticsearch_image(version):
    return SourceImage(
        name='elasticsearch',
        parent=Image(namespace='yandex', repository='trusty'),
        scripts=[
            'curl http://packages.elasticsearch.org/GPG-KEY-elasticsearch | apt-key add -',
            'echo "deb http://packages.elasticsearch.org/elasticsearch/{version_minor}/debian stable main"'
            ' > /etc/apt/sources.list.d/elasticsearch.list && apt-get update -q'.format(
                version_minor='.'.join(version.split('.')[:2])),
            'apt-get update -q && '
            'apt-get install -y --no-install-recommends maven elasticsearch={version_full} openjdk-7-jdk'.format(
                version_full=version),
            'git clone https://github.com/grmblfrz/elasticsearch-zookeeper.git /tmp/elasticsearch-zookeeper',
            'cd /tmp/elasticsearch-zookeeper && git checkout v{version_full} && '
            'mvn package -Dmaven.test.skip=true -Dzookeeper.version=3.4.6'.format(version_full=version),
            '/usr/share/elasticsearch/bin/plugin -v '
            '  -u file:///tmp/elasticsearch-zookeeper/target/releases/elasticsearch-zookeeper-{version_full}.zip '
            '  -i elasticsearch-zookeeper-{version_full}'.format(version_full=version),
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


def create_elasticsearch(clustername, version):
    """Returns Elasticsearch container.
    Tested versions: 1.3.1, 1.4.1
    """
    image = get_elasticsearch_image(version=version)
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
                files={'config.js':  None},
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
        volumes={
            'logs': '/var/log/nginx',
            'sites': '/etc/nginx/sites-enabled',
            'ssl': '/etc/nginx/certs',
        },
        command=['nginx'],
    )


def create_nginx_proxy():
    image = get_nginx_image()
    sites = ConfigVolume(
        dest=image.volumes['sites'],
        files={'elk.site': None},
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
        files={'server.pem': None},
    )
    container = Container(
        name='nginx',
        image=image,
        volumes={
            'sites': sites,
            'logs': logs,
            'ssl': ssl,
        },
        memory=1024**2*256,
    )

    ssl.files['server.pem'] = lambda: TextFile(container.ship.certificate)

    return container


def attach_upstreams_to_nginx(nginx, upstreams):

    def copyurls(door):
        """This function duplicates Url objects."""
        return {name: Url(url.path) for name, url in door.urls.items()}

    def generate_doors():
        for upstream, httpport, httpsport in upstreams:
            for schema, port in [('http', httpport), ('https', httpsport)]:
                name = upstream.container.name + '.' + schema
                door = Door(
                    schema=schema,
                    port=port,
                    urls=copyurls(upstream),
                )
                yield name, door

    def make_nginx_site_config(nginx=nginx, upstreams=upstreams):
        template = resource_string('elk.site')
        config = mako.template.Template(template).render(
            upstreams=upstreams,
            certificate_path=os.path.join(nginx.volumes['ssl'].dest, 'server.pem'),
        )
        return TextFile(config)

    nginx.volumes['sites'].files['elk.site'] = make_nginx_site_config
    nginx.doors.update(dict(generate_doors()))
    nginx.links.update({door.container.name: door.urls['default'] for door in nginx.doors.values()})


def create_dump_task():
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
            entrypoint=['/usr/local/bin/elasticdump'],
        ),
    )
    return dump


def attach_elasticsearch_to_kibana(kibana, httpdoor, httpsdoor):
    """Adds Elasticsearch ports to Kibana config.
    Ports should be already exposed for this to work.
    """
    kibana.links['elasticsearch.http'] = httpdoor
    kibana.links['elasticsearch.https'] = httpsdoor

    config_template = resource_string('config.js')
    config = mako.template.Template(config_template).render(
        http_port=httpdoor.port,
        https_port=httpsdoor.port,
    )
    kibana.volumes['config'].files['config.js'] = TextFile(config)


def attach_zookeepers_to_elasticsearch(elasticsearch, zookeepers):
    zkdoors = [zookeeper.doors['client'] for zookeeper in zookeepers]
    elasticsearch.links['zookeeper'] = zkdoors


def test(shipment):
    """Use this function as a skeleton for deploing Elasticsearch cluster."""
    shipment.unload_ships()
    ships = shipment.ships.values()

    zookeepers = build_zookeeper_cluster(filter_quorum_ships(ships))

    elasticsearches = []
    for ship in ships:
        elasticsearch = create_elasticsearch(clustername='testcluster', version='1.4.1')
        kibana = create_kibana()
        nginx = create_nginx_proxy()

        # Place all containers on the same ship
        ship.place(elasticsearch)
        ship.place(kibana)
        ship.place(nginx)

        # Adjust memory to prevent OOM when running on the laptop
        elasticsearch.memory = min(ship.memory, 128*1024*1024)

        # We will use Zookeeper for master discovery
        attach_zookeepers_to_elasticsearch(elasticsearch, zookeepers)

        # Nginx will terminate https and proxy requests to Elasticsearch and Kibana
        attach_upstreams_to_nginx(nginx, upstreams=[
            (elasticsearch.doors['http'], 9200, 9443),
            (kibana.doors['http'], 8080, 8443),
        ])

        # Expose nginx ports before attaching it to Kibana
        nginx.expose_ports(list(range(2000, 4000)))

        # Kibana should use the same origin and just change port to access Elasticsearch
        attach_elasticsearch_to_kibana(kibana, httpdoor=nginx.doors['elasticsearch.http'],
                                       httpsdoor=nginx.doors['elasticsearch.https'])

        # Save elasticsearches to clusterize them later
        elasticsearches.append(elasticsearch)

    # Let Elasticsearches know about each other
    clusterize_elasticsearches(elasticsearches)

    # Expose all unexposed ports
    shipment.expose_ports(list(range(51000, 51100)))
