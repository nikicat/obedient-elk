#!/bin/sh

NAME=elasticsearch

# Directory where the Elasticsearch binary distribution resides
ES_HOME=/usr/share/$NAME

# Heap Size (defaults to 256m min, 1g max)
#ES_HEAP_SIZE=2g

# Heap new generation
#ES_HEAP_NEWSIZE=

# max direct memory
#ES_DIRECT_SIZE=

# Additional Java OPTS
#ES_JAVA_OPTS=

# Maximum number of open files
MAX_OPEN_FILES=500000

# Maximum amount of locked memory
#MAX_LOCKED_MEMORY=

# Elasticsearch log directory
LOG_DIR=/var/log/$NAME

# Elasticsearch data directory
DATA_DIR=/var/lib/$NAME

# Elasticsearch work directory
WORK_DIR=/tmp/$NAME

# Elasticsearch configuration directory
CONF_DIR=/etc/$NAME

# Elasticsearch configuration file (elasticsearch.yml)
CONF_FILE=$CONF_DIR/elasticsearch.yml

# Maximum number of VMA (Virtual Memory Areas) a process can own
MAX_MAP_COUNT=262144

# Define other required variables
export JAVA_OPTS="-server -showversion \
    -Des.default.config=$CONF_FILE \
    -Des.default.path.home=$ES_HOME \
    -Des.default.path.logs=$LOG_DIR \
    -Des.default.path.data=$DATA_DIR \
    -Des.default.path.work=$WORK_DIR \
    -Des.default.path.conf=$CONF_DIR \
    -Dcom.sun.management.jmxremote.authenticate=false \
    -Dcom.sun.management.jmxremote.ssl=false \
    -Dcom.sun.management.jmxremote.local.only=false \
    -Dcom.sun.management.jmxremote.port=$JAVA_RMI_PORT \
    -Dcom.sun.management.jmxremote.rmi.port=$JAVA_RMI_PORT \
    -Djava.rmi.server.hostname=$JAVA_RMI_SERVER_HOSTNAME"

export ES_CLASSPATH=/etc/elasticsearch/logging.yml

$ES_HOME/bin/elasticsearch
