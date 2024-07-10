#!/bin/sh

# from github workflow

UBUNTU="
  python3 -m venv venv && . venv/bin/activate
  pip3 install --upgrade pip wheel setuptools
  pip3 install -r requirements.txt
  pip3 install pyinstaller sysv-ipc geoip2 flask
  WEBSPY=0 ./build.sh && mkdir -p ./build-ubuntu && mv -f *.tar.gz *.sha512sum ./build-ubuntu
"
DEBIAN="
  DEBIAN_FRONTEND=noninteractive apt-get update -y;
  apt-get install -y wget python3 python3-venv python3-pip
  apt-get install -y upx-ucl || {
    apt-get install -y libucl1 &&
    wget -q http://ftp.us.debian.org/debian/pool/main/u/upx-ucl/upx-ucl_3.96-3+b1_amd64.deb &&
    dpkg -i upx-ucl_3.96-3+b1_amd64.deb;
  };
  python3 -m venv venv && . venv/bin/activate
  pip3 install --upgrade pip wheel setuptools
  pip3 install -r requirements.txt
  pip3 install pyinstaller sysv-ipc geoip2 flask
  WEBSPY=0 ./build.sh && mkdir -p ./build-debian && mv -f *.tar.gz *.sha512sum ./build-debian
"
#CENTOS="
#  yum install -y gcc python3-devel python3-pip python3-virtualenv
#  yum install -y https://download-ib01.fedoraproject.org/pub/epel/7/x86_64/Packages/u/ucl-1.03-24.el7.x86_64.rpm
#  yum install -y https://download-ib01.fedoraproject.org/pub/epel/7/x86_64/Packages/u/upx-3.96-9.el7.x86_64.rpm
#  python3 -m venv venv && . venv/bin/activate
#  pip3 install --upgrade pip wheel setuptools
#  pip3 install -r requirements.txt
#  pip3 install pyinstaller sysv-ipc geoip2
#  WEBSPY=0 ./build.sh  && mkdir -p ./build-centos && mv -f *.tar.gz *.sha512sum ./build-centos
#"
ALPINE="
  apk add python3 python3-dev py3-pip py3-virtualenv gcc musl-dev upx
  python3 -m venv venv && . venv/bin/activate
  pip3 install --upgrade pip wheel setuptools
  pip3 install -r requirements.txt
  pip3 install pyinstaller sysv-ipc geoip2 flask
  WEBSPY=0 ./build.sh && mkdir -p ./build-alpine && mv -f *.tar.gz *.sha512sum ./build-alpine
"
RHEL="
  dnf install -y gcc python3-devel python3-pip
  dnf install -y epel-release
  dnf install -y upx ucl
  python3 -m venv venv && . venv/bin/activate
  pip3 install --upgrade pip wheel setuptools
  pip3 install -r requirements.txt
  pip3 install pyinstaller sysv-ipc geoip2
  ./build.sh && mkdir ./build-$1 && mv -f *.tar.gz *.sha512sum ./build-$1
"

func_docker_run() {
  image=$1
  shift
  docker run -it --rm  --workdir /build -v "$PWD:/build" "$image" sh -c "$*"
}

case $1 in
  ubuntu) func_docker_run ubuntu24.04 "$UBUNTU" ;;
  debian) func_docker_run debian:12 "$DEBIAN" ;;
  centos7) func_docker_run centos:7 "$CENTOS" ;;
  centos-stream) func_docker_run quay.io/centos/centos:stream9 "$RHEL" ;;
  alma) func_docker_run almalinux:9.4 "$RHEL" ;;
  rocky) func_docker_run rockylinu:9.3 "$RHEL" ;;
  alpine) func_docker_run alpine:3.18 "$ALPINE" ;;
  *) echo "$0 $(grep -E ' *[a-z]+\).*;;' "$0" | cut -d')' -f1 | tr -d '\n')" ;;
esac
