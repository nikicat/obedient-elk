import setuptools

if __name__ == '__main__':
    setuptools.setup(
        name='obedient.elk',
        version='1.2.0',
        url='https://github.com/yandex-sysmon/obedient.elk',
        license='LGPLv3',
        author='Nikolay Bryskin',
        author_email='devel.niks@gmail.com',
        description='ELK (Elasticsearch-Logstash-Kibana) obedient for Dominator',
        platforms='linux',
        packages=['obedient.elk'],
        namespace_packages=['obedient'],
        package_data={'obedient.elk': [
            'config.js',
            'elk.site',
            'nginx.conf',
            'logging.yml',
            'mapping.json',
            'elasticsearch.sh',
        ]},
        entry_points={'obedient': [
            'create = obedient.elk:create_elk',
            'test = obedient.elk:test',
        ]},
        install_requires=[
            'dominator[full] >=15a',
            'obedient.zookeeper >=3a',
        ],
    )
