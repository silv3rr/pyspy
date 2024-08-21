#!/bin/sh

# Build pyspy binaries with pyinstaller

# Usage:
#   ./build.sh _WITH_GEOIP _WITH_HTTPD _WITH_FLASK _WITH_BUNDLE

# Required: Python 3.7.3+, sysv-ipc, pyinstaller
#   python3 -m venv venv && . venv/bin/activate && \
#   pip3 install wheel setuptools sysv_ipc pyinstaller

PYREQVER="3.7"
PYSRC="spy.py"
PYINSTALLER=1
PACK=1
REQS="$(cut -d= -f1 requirements.txt 2>/dev/null)"
ARGS="--hidden-import sysv_ipc"
OPTS="_WITH_GEOIP _WITH_HTTPD _WITH_FLASK _WITH_BUNDLE"
PACKFILES="../spy.conf spy ../webspy"

if [ ! -s requirements.txt ] || [ -z "$REQS" ]; then
  echo "build: WARNING missing requirements"
fi

for a in "$@"; do
  if echo "$a" | grep -iq -- "-h"; then
    printf "./%s %s\n" "$(basename "$0")" "$OPTS"
    exit 0
  fi
  if echo "$a" | grep -q "_WITH_GEOIP"; then
    ARGS="--hidden-import geoip2.webservice"
    REQS="$REQS geoip2"
  fi
  if echo "$a" | grep -q "_WITH_FLASK"; then
    ARGS="--hidden-import flask"
    REQS="$REQS flask"
  fi
  if echo "$a" | grep -q "_WITH_BUNDLE"; then
    echo "build: including webspy dir in pyinstaller bundle..."
    ARGS=" $ARGS --add-data webspy:./webspy "
    PACKFILES="../spy.conf spy"
    PACKSUFFIX="-web"
  fi
  for o in $OPTS; do
    if echo "$a" | grep -q "$o"; then
      if grep -Eiq "^$a *= *false$" "$PYSRC"; then
        sed -i 's/^\('"$a"'\) *= *.*$/\1 = True/' "$PYSRC" &&
          echo "build: set $a to True"
      fi
    fi
  done
done

# disable flask dev
sed -i "s/^\(FLASK_OPTIONS\['debug']\) *= *.*$/\1 = False/" "$PYSRC"

echo "build: creating one single executable file..."

if [ -n "$VIRTUAL_ENV" ]; then
  echo "build: running in venv: ${VIRTUAL_ENV}..."
else
  echo "build: not running in venv..."
fi

if [ ! -e "$PYSRC" ]; then
  echo "build: ERROR '$PYSRC' not found"
  exit 1
fi

printf "build: "
command -V python3 || {
  echo "build: ERROR python3 not found"
  exit 1
}

PYVER="$(python3 --version | sed 's/.* \([0-9]\.[0-9]\{1,2\}\).*/\1/' | grep -E '^[0-9.]+$' || echo 0)"
PYVER_OK=0
PYVER_MAY="$(echo "$PYVER" | sed 's/\([0-9]\)\.[0-9]\+/\1/')"
PYVER_MIN="$(echo "$PYVER" | sed 's/[0-9]\.\([0-9]\+\)/\1/')"
PYREQVER_MAY="$(echo $PYREQVER | sed 's/\([0-9]\)\.[0-9]/\1/')"
PYREQVER_MIN="$(echo $PYREQVER | sed 's/[0-9]\.\([0-9]\+\)/\1/')"
if [ "$PYVER_MAY" -gt "$PYREQVER_MAY" ]; then
  PYVER_OK=1
elif [ "$PYVER_MAY" -eq "$PYREQVER_MAY" ] && [ "$PYVER_MIN" -ge "$PYREQVER_MIN" ]; then
  PYVER_OK=1
fi
if [ "$PYVER_OK" -eq 1 ]; then
  echo "build: python version is OK (need Python ${PYREQVER}+ got v${PYVER})"
else
  echo "build: WARNING Python ${PYREQVER}+ not found"
fi

ECNT=0
for i in $REQS; do
  PKG="$( echo "$i" | tr '_' '-' )"
  printf "%b\n" 'try:\n  import '"${i}"'\nexcept:\n  exit(1)' | python3 || {
    echo "build: module '${i}' not found, on debian try 'apt install python3-${PKG}' or 'pip install ${PKG}'"
    ECNT=$((ECNT + 1))
  }
done
if [ "$ECNT" -gt 0 ]; then
  echo "build: ERROR $ECNT module(s) missing"
  exit 1
fi

if [ "$PYINSTALLER" -eq 1 ]; then
  command -v pyinstaller >/dev/null 2>&1 || {
    echo "build: ERROR pyinstaller not found, on debian try 'apt install python3-pyinstaller' or 'pip install pyinstaller'"
    exit 1
  }
  # shellcheck disable=SC2086
  pyinstaller spy.py $ARGS --clean --noconfirm --onefile &&
    if [ -e "dist/spy" ]; then
      printf "\nbuild: result OK "
      ls -la dist/spy
      echo
      if [ "$PACK" -eq 1 ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        PACKNAME="pyspy-${ID:-linux}${VERSION_ID}-python${PYVER:-3}-x86_x64${PACKSUFFIX}"
        printf "build: Creating %s.tar.gz...\n" "$PACKNAME"
        tar -C ./dist -cvf "${PACKNAME}.tar.gz" $PACKFILES >/dev/null &&
          sha512sum "${PACKNAME}.tar.gz" >"${PACKNAME}.sha512sum" && echo "build: shasum OK" || echo "build: ERROR shasum"
      fi
    else
      echo
      echo "build: ERROR something went wrong :("
      exit 1
    fi
fi

# reset to defaults
sed -i "s/^\(_WITH_GEOIP\) *= *.*$/\1 = False/" "$PYSRC"
sed -i "s/^\(_WITH_HTTPD\) *= *.*$/\1 = True/" "$PYSRC"
sed -i "s/^\(_WITH_FLASK\) *= *.*$/\1 = True/" "$PYSRC"
sed -i "s/^\(_WITH_BUNDLE\) *= *.*$/\1 = False/" "$PYSRC"
