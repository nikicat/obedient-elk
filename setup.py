import setuptools

if __name__ == '__main__':
    setuptools.setup(
        name='obedient.elk',
        version='0.5',
        url='https://github.com/yandex-sysmon/obedient.elk',
        license='LGPLv3',
        author='Nikolay Bryskin',
        author_email='devel.niks@gmail.com',
        description='ELK (Elasticsearch-Logstash-Kibana) obedient for Dominator',
        platforms='linux',
        packages=['obedient.elk'],
        namespace_packages=['obedient'],
        package_data={'obedient.elk': ['config.js', 'elk.site', 'nginx.conf']},
        install_requires=[
            'dominator[full] >=7',
            'obedient.elasticsearch >=1.3',
            'obedient.zookeeper >=1.2',
        ],
    )
