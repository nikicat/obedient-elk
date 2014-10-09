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

# Maximum number of VMA (Virtual Memory Areas) a process can own
MAX_MAP_COUNT=262144

source /etc/elasticsearch/env.sh
export ES_CLASSPATH=/etc/elasticsearch/logging.yml

$ES_HOME/bin/elasticsearch
