import re
from dominator import *
from elasticlog import make_elasticlog

@aslist
def fill_datacenter(ships):
    import transliterate
    for ship in ships:
        ship.datacenter = transliterate.translit(datacenter_from_racktables(ship.fqdn), 'ru', reversed=True).replace(' ', '-')
        yield ship


@aslist
def override_data_path(containers):
    for container in containers:
        data = container.getvolume('data')
        data.path = data.path.replace('/var/lib', get_data_path(container.ship))
        yield container

def get_data_path(ship):
    if isinstance(ship, LocalShip):
        return '/tmp'
    elif re.match('node..e\.elasticlog\.yandex\.net', ship.fqdn):
        return '/local'
    elif re.match('node..d\.elasticlog\.yandex\.net', ship.fqdn):
        return '/var/lib'
    elif re.match('elastic.\.i\.fog\.yandex\.net', ship.fqdn):
        return '/mnt'


def production():
    return override_data_path(make_elasticlog(fill_datacenter(
                              ships_from_conductor('elasticlog-sysmon')), 'elasticlog-sysmon'))

def testing():
    return make_elasticlog(fill_datacenter(ships_from_nova('haze', {'elasticlog': 'testing'})), 'elasticlog-testing')

def development():
    return override_data_path(make_elasticlog([LocalShip()], 'elasticlog-local'))
