[![Build pyspy](https://github.com/silv3rr/pyspy/actions/workflows/build.yml/badge.svg)](https://github.com/silv3rr/pyspy/actions/workflows/build.yml)

# SPY.PY

## /spai.pai/

Shows users logged into glftpd in a terminal or as web page. Like 'gl_spy' and also 'webspy' from foo-tools.

[screenshot_1](docs/pyspy1.png)

Used to be included with [pywho](https://github.com/silv3rr/pywho) but is now its own separate thing.

Part of [docker-glftpd](https://github.com/silv3rr/docker-glftpd)'s web interface.


## Usage

Running `./spy` without args starts `--cli` mode (default)

``` bash
./spy --web     # run webspy using flask (css, templates & js)
./spy --httpd   # run webspy using built in httpd (basic)
```

## Installation

Only latest glftpd version 2.12+ is supported (other versions untested)

Pick one of these 3 setup methods

## apt

- `apt install python3-sysv-ipc`
- `apt install python3-geoip2 python3-flask`  (optional)
- `git clone` this repo and run script: `./pywho.py`

## venv/pip

``` bash
# Install py3/venv pkgs first, e.g. for debian/redhat:
apt install python3-pip python3-venv
yum install python3-pip python3-virtualenv

python3 -m venv venv
source venv/bin/activate
pip3 install sysv-ipc
pip3 install geoip2 flask  # optional
```

Now 'git clone' this repo and run `./spy.py`

_If you want to build sysv_ip from src, see [https://github.com/osvenskan/sysv_ip](https://github.com/osvenskan/sysv_ipc)_

## binaries

[![Build pyspy](https://github.com/silv3rr/pyspy/actions/workflows/build.yml/badge.svg)](https://github.com/silv3rr/pyspy/actions/workflows/build.yml)

If you do not want to install python modules, there's also a single executable file available for [download](../../releases).

Supported: CentOS 7, Debian 11, 12 and Ubuntu 20.04, 22.04

Goto [Releases](../../releases) tab for all files

## Configuration

To set gl paths, theme for cli mode, web server ip/port etc edit 'spy.conf'.

All options are explained at the bottom of conf. Make sure 'ipc_key' matches glftpd.

To change how flask webspy looks, edit static/style.css and templates/*.html 

## Build

To build the pyspy binary yourself you need PyInstaller. You probably want to setup and activate a virtual env first (see above) then `pip install sysv-ipc pyinstaller`.

Now clone this repo and run build.sh, optionally add one or more of these args:

`_WITH_GEOIP _WITH_HTTPD _WITH_FLASK`

The build script will check and warn about wrong python version and missing modules.

## Issues

- If geoip2 is enabled you can run out of your free geoip queries
    - Max is 1000/day, ip lookups are cached in mem only and reset on restart of pyspy

- "CLI mode sucks! it doesnt work, updates slowly, ignores key presses, text gets fucked up"
    - Well, yeah, it uses simple ansi escape sequences and select() stdin instead of curses and input events etc..
