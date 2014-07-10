import setuptools

if __name__ == '__main__':
    setuptools.setup(
        name='obedient.elk',
        version='0.2',
        url='https://github.com/yandex-sysmon/obedient-elk',
        license='GPLv3',
        author='Nikolay Bryskin',
        author_email='devel.niks@gmail.com',
        description='ELK (Elasticsearch-Logstash-Kibana) obedient for Dominator',
        platforms='linux',
        packages=['obedient.elk'],
        namespace_packages=['obedient', 'obedient.elk'],
        data_files={'obedient.elk': ['config.js', 'elk.site', 'nginx.conf']},
        install_requires=[
            'dominator[dump] >=4, <5',
            'obedient.elasticsearch',
            'obedient.zookeeper',
        ],
    )
