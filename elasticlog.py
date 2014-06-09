import re

from dominator import *

def get_data_path(ship):
    if isinstance(ship, LocalShip):
        return '/tmp'
    elif re.match('node..e\.elasticlog\.yandex\.net', ship.fqdn):
        return '/local'
    elif re.match('node..d\.elasticlog\.yandex\.net', ship.fqdn):
        return '/var/lib'
    elif re.match('elastic.\.i\.fog\.yandex\.net', ship.fqdn):
        return '/mnt'
    elif re.match('.*\.haze\.yandex\.net', ship.fqdn):
        return '/var/lib'

containers = []


@aslist
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

@aslist
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
    for datacenter, ships in groupby(ships, lambda s: s.datacenter):
        yield list(ships)[0]


def make_elasticlog(ships, name):
    elasticsearchs = make_elasticsearchs(ships, name)
    zookeepers = make_zookeepers(zookeeper_ships(ships), name)

    global containers
    containers += elasticsearchs + zookeepers
    return containers

@aslist
def fill_datacenter(ships):
    import transliterate
    for ship in ships:
        ship.datacenter = transliterate.translit(datacenter_from_racktables(ship.fqdn), 'ru', reversed=True).replace(' ', '-')
        yield ship

def production():
    return make_elasticlog(fill_datacenter(ships_from_conductor('elasticlog-sysmon')), 'elasticlog-sysmon')

def testing():
    return make_elasticlog(fill_datacenter(ships_from_nova('haze', {'elasticlog': 'testing'})), 'elasticlog-testing')

def development():
    return make_elasticlog([LocalShip()], 'elasticlog-local')
