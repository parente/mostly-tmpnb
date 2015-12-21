#!/bin/bash

# From https://jpetazzo.github.io/2015/01/13/docker-mount-dynamic-volumes/
# with minor modifications

set -e
if [ -z "$CONTAINER" ]; then
    echo "ERROR: CONTAINER not defined"
    exit 1
fi
if [ -z "$VOLUME" ]; then
    echo "ERROR: VOLUME not defined"
    exit 1
fi
if [ -z "$HOSTMOUNT" ]; then
    echo "ERROR: HOSTMOUNT not defined"
    exit 1
fi

echo "HOSTMOUNT: $HOSTMOUNT"
echo "VOLUME: $VOLUME"
echo "CONTAINER: $CONTAINER"

HOSTPATH="$HOSTMOUNT$(docker volume inspect --format '{{ .Mountpoint }}' $VOLUME)"
echo "HOSTPATH: $HOSTPATH"
CONTPATH=/home/jovyan/work
echo "CONTPATH: $CONTPATH"

# Because we mounted the host under /host in a container!
REALPATH=$(readlink --canonicalize $HOSTPATH)
echo "REALPATH: $REALPATH"
FILESYS=$(df -P $REALPATH | tail -n 1 | awk '{print $6}')
echo "FILESYS: $FILESYS"

while read DEV MOUNT JUNK
do [ $MOUNT = $FILESYS ] && break
done </proc/mounts
[ $MOUNT = $FILESYS ] # Sanity check!
DEV=$(readlink --canonicalize $HOSTMOUNT$DEV)
echo "DEV: $DEV (with host mount)"

while read A B C SUBROOT MOUNT JUNK
do [ $MOUNT = $FILESYS ] && break
done < /proc/self/mountinfo 
[ $MOUNT = $FILESYS ] # Moar sanity check!
echo "SUBROOT: $SUBROOT (sans host mount)"

SUBPATH=$(echo $REALPATH | sed s,^$FILESYS,,)
echo "SUBPATH: $SUBPATH"
DEVDEC=$(printf "%d %d" $(stat --format "0x%t 0x%T" $DEV))
echo "DEVDEC: $DEVDEC"

# Strip off the host mount so that we get the device path as it exists on the host
DEV=${DEV#$HOSTMOUNT}
echo "DEV: $DEV (without host mount)"

echo "Making device node"
docker-enter $CONTAINER sh -c \
         "[ -b $DEV ] || mknod --mode 0600 $DEV b $DEVDEC"
echo "Creating temporary device mount"
docker-enter $CONTAINER mkdir /tmpmnt
docker-enter $CONTAINER mount $DEV /tmpmnt
echo "Creating volume bind mount"
docker-enter $CONTAINER mkdir -p $CONTPATH
docker-enter $CONTAINER mount -o bind /tmpmnt/$SUBROOT/$SUBPATH $CONTPATH
docker-enter $CONTAINER chown jovyan:users $CONTPATH
echo "Removing temporary device mount"
docker-enter $CONTAINER umount /tmpmnt
docker-enter $CONTAINER rmdir /tmpmnt
echo "DONE: mounted $VOLUME as $CONTPATH in $CONTAINER"
