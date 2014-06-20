from dominator import *
from obedient import elasticsearch
from obedient import zookeeper


def zookeeper_ships(ships):
    for datacenter, ships in groupby(ships, lambda s: s.datacenter):
        yield list(ships)[0]


def make_containers(ships, name):
    zookeepers = zookeeper.make_containers(zookeeper_ships(ships))
    elasticsearchs = elasticsearch.make_containers(ships, zookeepers, name)

    return zookeepers + elasticsearchs
