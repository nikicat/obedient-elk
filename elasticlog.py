from dominator import *

def get_data_path(ship):
    if ship.islocal:
        return '/tmp'
    elif ship.name[-1] == 'e':
        return '/local'
    elif ship.name[-1] == 'd':
        return '/var/lib'
    else:
        return '/mnt'

containers = []

def make_elasticsearchs(ships, name):
    config = ConfigVolume(
        dest='/etc/elasticsearch',
        files = [
            TemplateFile('elasticsearch.yml', name=name, containers=containers),
            TextFile('mapping.json'),
            TextFile('logging.yml'),
        ],
    )

    for ship in ships:
        yield Container(
            name='elasticsearch',
            ship=ship,
            repository='nikicat/elasticsearch',
            tag='latest',
            volumes=[
                DataVolume(
                    dest='/var/lib/elasticsearch',
                    path=get_data_path(ship) + '/elasticsearch',
                ),
                config,
            ],
            ports={'http': 9200, 'peer': 9300},
            memory=ship.memory * 3 // 4,
        )


def make_zookeepers(ships, name):
    config = ConfigVolume(
        dest='/opt/zookeeper/conf',
        files = [
            TemplateFile('zoo.cfg', containers=containers),
            TemplateFile('myid', containers=containers),
            TextFile('log4j.properties'),
        ]
    )

    for ship in ships:
        yield Container(
            name='zookeeper',
            ship=ship,
            repository='nikicat/zookeeper',
            tag='latest',
            volumes=[
                DataVolume(
                    dest='/var/lib/zookeeper',
                    path=get_data_path(ship) + '/zookeeper',
                ),
                config,
            ],
            ports={'election': 3888, 'peer': 2888, 'client': 2181, 'jmx': 4888},
            memory=1024**3,
            env={
                'JAVA_OPTS': '-Xmx700m',
                'JAVA_RMI_SERVER_HOSTNAME': ship.fqdn,
                'VISUALVM_DISPLAY_NAME': '{}-{}'.format(name, 'zookeeper'),
            },
        )

def zookeeper_ships(ships):
    for ship in ships:
        if ship.name in ['elastic0', 'node01d', 'node01e'] or ship.islocal:
            yield ship


def make_elasticlog(ships, name):
    global containers

    containers.extend(make_elasticsearchs(ships, name))
    containers.extend(make_zookeepers(zookeeper_ships(ships), name))

    return containers

def production():
    return make_elasticlog(list(ships_from_conductor('elasticlog-sysmon')), 'elasticlog-sysmon')

def testing():
    return make_elasticlog(list(ships_from_conductor('elasticlog-sysmon')) + [Ship(name='docker-1', fqdn='docker-1.i.fog.yandex.net', datacenter='sas-1-1-3')], 'elasticlog-sysmon')

def development():
    return make_elasticlog([LocalShip()], 'elasticlog-local')
