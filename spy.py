#!/usr/bin/env python3

# pylint: disable=line-too-long, consider-using-f-string, c-extension-no-member

"""
################################################################################
# SPY.PY                                                                 # slv #
################################################################################
# Shows users logged into glftpd, in terminal or as web page                   #
# Like 'gl_spy' and 'webspy' from foo-tools                                    #
# Uses SHM and glftpd's 'ONLINE' C struct, module sysv_ipc is required         #
# See README and comments in spy.conf for install and config instructions      #
################################################################################
"""

import struct
import re
import time
import datetime
import configparser
import os
import sys
import socket
import calendar
import collections
import subprocess
import sysv_ipc


_WITH_GEOIP = False
_WITH_HTTPD = True
_WITH_FLASK = True

PYINSTALLER = False

if _WITH_HTTPD:
    import http.server
    import socketserver
    import threading

if _WITH_FLASK:
    import flask

if _WITH_GEOIP:
    import geoip2.webservice

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    PYINSTALLER = True

VERSION = "20230705"

SCRIPT_PATH = os.path.abspath(__file__) if os.path.abspath(__file__) else os.path.realpath(sys.argv[0])
if PYINSTALLER:
    SCRIPT_PATH = os.path.realpath(sys.argv[0])
SCRIPT = os.path.basename(sys.argv[0])
SCRIPT_NAME = os.path.splitext(SCRIPT)[0]
SCRIPT_DIR = os.path.dirname(SCRIPT_PATH)
UPLOADS = DOWNLOADS = 0
TOTAL_UP_SPEED = TOTAL_DN_SPEED = 0
ONLINEUSERS = BROWSERS = IDLERS = 0
SHOWALL = 0
GEOIP2_BUF = {}
GEOIP2_CLIENT = None

CLI_MODE = 1
HTTPD_MODE = 0
FLASK_MODE = 0
FLASK_PROXY= False
CLI_SEARCH = 0  # search username in list, not that usefull - disabled by default

SPY_VERSTR = f"spy-{VERSION}"
if _WITH_GEOIP:
    SPY_VERSTR += '-geoip'
if _WITH_HTTPD:
    SPY_VERSTR += '-httpd'
if _WITH_FLASK:
    SPY_VERSTR += '-flask'


# handle args
##############

if '-h' in sys.argv or '--help' in sys.argv:
    print(f'./{SCRIPT_NAME} [--cli|--httpd|--flask|--web]')
    sys.exit(0)
elif '-v' in sys.argv or '--version' in sys.argv:
    print(SPY_VERSTR)
    sys.exit(0)
elif len(sys.argv) > 1 and len(sys.argv[1]) in [5, 7]:
    if '--cli' in sys.argv or '--spy' in sys.argv:
        CLI_MODE = 1
        print('No need specify spy/cli mode as its the default')
    elif _WITH_HTTPD and sys.argv[1] == '--httpd':
        CLI_MODE = 0
        HTTPD_MODE = 1
    elif _WITH_FLASK and ('--web' in sys.argv or '--flask' in sys.argv):
        CLI_MODE = 0
        FLASK_MODE = 1
    else:
        sys.exit(0)
else:
    if len(sys.argv) > 1 and sys.argv[1][0] == '-':
        print("Error: invalid option, try '-h'\n")
        sys.exit(1)


# config file
##############

CONFIGFILE = f'{SCRIPT_DIR}/{SCRIPT_NAME}.conf'
CONFIG_FN = None
config = configparser.ConfigParser()
config_errors = []
# f'{os.getcwd()}/spy.conf'
for CONFIG_FN in set([CONFIGFILE, f'{SCRIPT_DIR}/spy.conf']):
    try:
        os.path.isfile(CONFIG_FN)
    except OSError as config_err:
        config_errors.append(config_err)
    else:
        break
try:
    with open(CONFIG_FN, 'r', encoding='utf-8', errors='ignore') as config_obj:
        config.read_string("[DEFAULT]\n" + config_obj.read())
except IOError as config_err:
    config_errors.append(config_err)

if len(config_errors) > 0:
    print('Error: opening config file')
    for config_err in config_errors:
        print(config_err)
        sys.exit(1)


try:
    glrootpath = config['DEFAULT']['glrootpath']
    ipc_key = config.get('DEFAULT', 'ipc_key', fallback='')
    maxusers = config.getint('DEFAULT', 'maxusers', fallback=20)
    nocase = config.getboolean('DEFAULT', 'case_insensitive', fallback=False)
    idle_barrier = config.getint('DEFAULT', 'idle_barrier', fallback=30)
    threshold = config.getint('DEFAULT', 'speed_threshold', fallback=1024)
    refresh  = config.getfloat('DEFAULT', 'refresh', fallback=1)
    color = config.getint('DEFAULT', 'color', fallback=1)
    debug = config.getint('DEFAULT', 'debug', fallback=0)
    httpd_host = config.get('WEB', 'httpd_host', fallback='localhost')
    httpd_port = config.getint('WEB', 'httpd_port', fallback=8080)
    flask_host = config.get('WEB', 'flask_host', fallback='localhost')
    flask_port = config.getint('WEB', 'flask_port', fallback=5000)
    geoip2_accountid = config['GEOIP']['geoip2_accountid']
    geoip2_licensekey = config['GEOIP']['geoip2_licensekey']
    geoip2_proxy = config.get('GEOIP', 'geoip_proxy', fallback=None)
    geoip2_enable = config.getboolean('GEOIP', 'geoip2_enable', fallback=False)
except (KeyError, configparser.InterpolationError) as conf_err:
    print(f'Error: check config file\n{conf_err}')
    sys.exit(1)

MAXUSERS = maxusers if maxusers else 0
THRESHOLD = threshold if threshold else 0
IDLE_BARRIER = idle_barrier if idle_barrier else 0
GEOIP2_ENABLE = geoip2_enable if geoip2_enable else False
REFRESH = refresh if refresh else 1
DEBUG = debug if debug else 0

if CLI_MODE:
    import signal
    import select
    import tty
    TTY_SETTINGS = tty.tcgetattr(sys.stdin)

if _WITH_FLASK:
    FLASK_OPTIONS = {}
    FLASK_OPTIONS['host'] = flask_host
    FLASK_OPTIONS['port'] = flask_port
    # flask dev mode
    if os.getenv("FLASK_DEBUG") == '1' or DEBUG > 1:
        FLASK_OPTIONS['debug'] = True
    if os.getenv("FLASK_THREADED") == '1':
        FLASK_OPTIONS['threaded'] = False
    if os.getenv("FLASK_PROXY") == '1':
        FLASK_PROXY = True

if _WITH_HTTPD:
    HTTPD_OPTIONS = (httpd_host, httpd_port)

if FLASK_PROXY:
    from werkzeug.middleware.proxy_fix import ProxyFix

if PYINSTALLER:
    FLASK_OPTIONS['debug'] = False


# glftpd data
##############

# shm and struct (default ipc_key: 0x0000dead=57005)
IPC_KEY = ipc_key if ipc_key else "0x0000DEAD"
KEY = int(IPC_KEY, 16)
NULL_CHAR = b'\x00'
if DEBUG > 3:
    print(
        f'DEBUG:\tIPC_KEY={IPC_KEY} KEY={KEY} sysv_ipc.SHM_RDONLY={sysv_ipc.SHM_RDONLY}\n',
        f'\tfmt = {KEY:#010x}', id(KEY)
    )

# converted from structonline.h and arranged like struct_ONLINE:
# tag(64s), username(24s), status(h) <...> procid(i)
STRUCT_FORMAT = ' \
  64s  24s  256s  h  256s  256s  i  i  \
  2i \
  2i \
  2I \
  2I \
  i  \
'

# pylint: disable=invalid-name
struct_ONLINE = collections.namedtuple(
    'struct_ONLINE',
    'tagline username status ssl_flag host currentdir groupid login_time \
    tstart_tv_sec tstart_tv_usec  \
    txfer_tv_sec txfer_tv_usec    \
    bytes_xfer1 bytes_xfer2       \
    bytes_txfer1 bytes_txfer2     \
    procid'
)

# set global var with gl's version
GL_VER = ""
try:
    glbin_popt = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with subprocess.Popen(f'{glrootpath}/bin/glftpd', **glbin_popt) as glbin_proc:
        glbin_out, glbin_err = glbin_proc.communicate()
        if not glbin_err:
            GL_VER = glbin_out.decode().split('\n', maxsplit=1)[0]
except FileNotFoundError:
    pass

# set global var with groupfile contents (/etc/group)
GROUPFILE = None
try:
    with open(f'{glrootpath}/etc/group', 'r', encoding='utf-8', errors='ignore') as grp_file:
        GROUPFILE = grp_file.readlines()
except IOError:
    pass

# set global var for users dir (/ftp-data/users)
USERS_DIR = None
for gl_udir_name in [f"{glrootpath}/ftp-data/users", "/ftp-data/users"]:
    if os.path.isdir(gl_udir_name):
        USERS_DIR=gl_udir_name
        break

# set global var for totalusers, use spy.conf or glftpd.conf's maxusers
glconf_maxusers = 0
for glconf_fn in [f'{glrootpath}/../glftpd.conf', f'{glrootpath}/glftpd.conf', '/etc/glftpd.conf']:
    try:
        with open(glconf_fn, 'r', encoding='utf-8', errors='ignore') as glconf_obj:
            for glconf_line in glconf_obj.readlines():
                if re.search(r'^max_users \d', glconf_line):
                    for mu_cnt in glconf_line.split()[1:]:
                        glconf_maxusers += int(mu_cnt)
                    break
    except IOError:
        pass
TOTALUSERS = 0
if MAXUSERS == -1 and glconf_maxusers > 0:
    TOTALUSERS = glconf_maxusers
else:
    TOTALUSERS = maxusers

HELP_TEXT  = """
  Main screen:
    Up/Down keys scroll in user list, and PgUp/PgDn/Home/End
    Shortcut keys '0-9' will jump to user list <num>

  User info:
    Press 'v', ENTER or Right to view detailed user info
      Use 'n' and 'p' or Left/Right for next/previous login
      Use 'k' to kick selected user *needs root*
      ESC returns to main screen

  Press 'q' key or CTRL-C to Quit

"""


# geoip
########

if _WITH_GEOIP and GEOIP2_ENABLE:
    GEOIP2_CLIENT = geoip2.webservice.Client(
        geoip2_accountid,
        geoip2_licensekey,
        host='geolite.info',
        proxy=geoip2_proxy if geoip2_proxy not in [None, 'None'] else None
    )


# classes
###########

class User:
    """ user struct as namedtuple, calc stats """
    uploads = UPLOADS
    downloads = DOWNLOADS
    total = 0
    total_up_speed = TOTAL_UP_SPEED
    total_dn_speed = TOTAL_DN_SPEED
    total_speed = 0
    browsers = BROWSERS
    idlers = IDLERS
    geoip2_client = GEOIP2_CLIENT if GEOIP2_CLIENT else None
    geoip2_shown_err = False
    tls_mode = [
        'None',       # no ssl
        'Control',    # ssl on control
        'Both'        # ssl on control and data
    ]

    def __init__(self, user_tuple, online=0):
        self.user_tuple = user_tuple
        self.name = self.get_name()
        self.group = self.get_group()
        self.online = online
        self.mb_xfered = self.get_mb_xfered()
        (self.addr, self.ip) =  self.get_ip()

    def get_name(self) -> str:
        """ get username from tuple """
        return self.user_tuple.username.split(NULL_CHAR, 1)[0].decode()

    def get_group(self) -> str:
        """ get group name using gid from tuple """
        if self.user_tuple.groupid >= 0:
            return get_group(self.user_tuple.groupid)
        return ""

    def get_ip(self) -> tuple:
        """ get adress and ip using host from tuple """
        addr = ""
        ip = "0.0.0.0"
        if self.user_tuple.host != '':
            (_, addr) = self.get('host').split('@', 2)[0:2]
            # ipv4/6
            if (''.join((addr).split('.', 3)).isdigit()) or (':' in addr):
                ip = addr
            # addr is not a fqdn
            elif '.' not in addr:
                ip = '127.0.0.1' if addr == 'localhost' else '0.0.0.0'
            else:
                try:
                    ip = socket.gethostbyname(addr)
                except OSError:
                    pass
        return (addr, ip)

    def get_bytes_xfer(self) -> int:
        """ bytes_xfer: convert 2 uint32 to uint64 """
        return self.get('bytes_xfer2') * pow(2, 32) + self.get('bytes_xfer1')

    def get_bytes_txfer(self) -> int:
        """ bytes_txfer: convert 2 uint32 to uint64 """
        return self.get('bytes_txfer2') * pow(2, 32) + self.get('bytes_txfer1')

    def get_mb_xfered(self) -> int:
        """ convert tranfered bytes to mb """
        return (abs(self.get_bytes_xfer() / 1024 / 1024)) if self.get_bytes_xfer() else 0

    def get_traf_dir(self) -> str:
        """ traffic direction """
        if self.get_bytes_xfer():
            if self.get('status')[:4] == 'RETR':
                return "Dn"
            if self.get('status')[:4] == 'STOR' or self.get('status')[:4] == 'APPE':
                return "Up"

    def get(self, attr):
        """ get attributes from tuple """
        r = getattr(self.user_tuple, attr)
        if DEBUG > 1:
            print(f'DEBUG: user.get() attr={attr} type={type(r)}')
        if isinstance(r, bytes):
            return r.split(NULL_CHAR, 1)[0].decode()
        return r


class Handler(http.server.BaseHTTPRequestHandler):
    """ HTTP Requests """
    def do_GET(self):
        """ GET Method """
        head = [
            "<!DOCTYPE html><html lang='en'>",
            "<head>",
            "  <title>webspy | http.server</title>",
            "  <style>",
            "    html { font-family: 'Courier New', monospace; }",
            "  </style>",
            " <meta http-equiv='Refresh' content='1'>",
            "</head>",
            "<body>",
        ]
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        for line in head:
            self.wfile.write(bytes(f"{line}\n", "utf-8"))
        self.wfile.write(bytes(format_html(), "utf-8"))
        self.wfile.write(bytes("\n</body>\n", "utf-8"))
        self.wfile.write(bytes("</html>\n", "utf-8"))


class TCPServerThread(threading.Thread):
    """ Thread for http server """
    def run(self):
        """ Start server """
        with socketserver.TCPServer((HTTPD_OPTIONS), Handler, False) as httpd:
            httpd.allow_reuse_address = True
            httpd.server_bind()
            httpd.server_activate()
            httpd.serve_forever()


class Esc:
    """ return ANSI ESC code sequence
        https://gist.github.com/fnky/458719343aabd01cfb17a3a4f7296797
           '#H': move cursor to home position (0, 0)
           '#A': move cursor up <num> lines
           '#C': move cursor right <num> columns
           '#E': start of next line, <num> lines down
           '#F': start of prev line, <num> lines up
           '0J': erase from cursor until end of screen
           '2J': erase entire screen
    """
    @staticmethod
    def __new__(cls, code):
        return f'\N{ESC}[{code}'


class Style:
    """ return escape code sequence or empty string if color is off """
    @staticmethod
    def __new__(cls, mode):
        if color == 0:
            return ''
        return {
            'r':    '\x1b[0m',      # reset all
            'b':    '\x1b[1m',      # bold
            'u':    '\x1b[4m',      # underline
            'bl':   '\x1b[5m',      # blink
            'rb':   '\x1b[22m',     # reset bold
        }[mode]


class Color:
    """ return color code or empty string if color is off """
    """ use UPPERCASE code for bright colors 0-7 """
    map = {
        'k':    0,      # black
        'r':    1,      # red
        'g':    2,      # green
        'y':    3,      # yellow
        'b':    4,      # blue
        'm':    5,      # magenta
        'c':    6,      # cyan
        'w':    7,      # white
        'd':    9,      # default
    }
    @staticmethod
    def __new__(cls, code):
        if color == 0:
            return ''
        fg = bg = 'd'
        try:
            (fg, bg) = code.split(',')
        except ValueError:
            pass
        return "{0}{1};{2}m".format(
            '\x1b[',
            f'{Color.map[fg] + 30}' if fg.islower() else f'{Color.map[fg.lower()] + 90}',
            f'{Color.map[bg] + 40}' if bg.islower() else f'{Color.map[bg.lower()] + 100}'
        )


class Theme:
    """ theme for cli mode """

    # unicode line drawing chars:
    draw = {
        'h':  '\U00002500',     # horizontal -
        'v':  '\U00002502',     # vertical   |
        'ul': '\U0000250C',     # up left
        'ur': '\U00002510',     # up right
        'dl': '\U00002514',     # down left  |_
        'dr': '\U00002518',     # down right _|
        'vl': '\U00002524',     # vertical left  -|
        'vr': '\U0000251C'      # vertical right |-
    }

    maincolor = f"{Style('b')}{Color('k,d')}" # gray/black
    logocolor = f"{Style('b')}{Color('m,d')}" # magenta
    delimiter = f"{maincolor}|{Style('r')}"   # pipe '|'
    vchar = f"{maincolor}{draw['v']}{Style('r')}"

    def __init__(self):
        self.columns = self.get_columns()
        self.lines = self.get_lines()
        self.max_col = self.columns - 11
        self.fill = " " * self.columns if self.columns > 80 else ""
        self.separator = self.fmt_separator()
        self.header = self.fmt_header()
        self.footer = self.fmt_footer()
        self.spacer = self.fmt_spacer()

    def get_columns(self) -> int:
        """ get term width """
        return os.get_terminal_size().columns if os.get_terminal_size().columns > 1 else 80

    def get_lines(self) -> int:
        """ get term height """
        return os.get_terminal_size().lines if os.get_terminal_size().lines > 1 else 25

    def fmt_separator(self) -> str:
        """ format separator """
        # '|-----------------|'
        draw = self.draw
        return "{mc}{vr}{hline}{vl}{rst}".format(
            mc=self.maincolor, vr=draw['vr'], hline=draw['h']*(self.columns-9),
            vl=draw['vl'], rst=Style('r')
        )
    def fmt_header(self) -> str:
        """ format header """
        draw = self.draw
        return "{mc}{ul}{hline}{ur}{rst}".format(
            mc=self.maincolor, ul=draw['ul'], ur=draw['ur'],hline=draw['h']*(self.columns-9), rst=Style('r')
        )
    def fmt_footer(self) -> str:
        """ format footer """
        draw = self.draw
        return "{mc}{dl}{hline}[{lc}PY-SPY{mc}]{h}{h}{h}{dr}{rst}".format(
            mc=self.maincolor, dl=draw['dl'], hline=draw['h']*(self.columns-20),
            lc=self.logocolor, h=draw['h'], dr=draw['dr'], rst=Style('r')
        )

    def fmt_spacer(self) -> str:
        """ format spacer  """
        # '| <~~~ space ~~~> |'
        return "{v} {sp:<{col}.{col}} {v}".format(v=self.vchar, sp=' ', col=self.max_col)

    def fmt_uinfo_title(self, text, col) -> str:
        """ format title on userinfo screen """
        return "{v} {text:<{col}.{col}} {v}".format(
            v=self.vchar, text=f"{Style('u')}{text}{Style('r')}", col=col
        )

    def fmt_uinfo_line(self, text, col) -> str:
        """ format line on userinfo screen """
        return "{v} {sp:>4.4}{text:<{col}.{col}} {v}".format(v=self.vchar, sp=' ', text=text, col=col)


# functions
############

def get_group(gid) -> str:
    """ get group name using gid """
    if GROUPFILE:
        for line in GROUPFILE:
            if line.split(':')[2] == str(gid):
                return line.split(':')[0]
    return ""


def get_gid(g_name) -> int:
    """ get group id using name """
    if GROUPFILE:
        for line in GROUPFILE:
            if line.split(':')[0] == g_name:
                return line.split(':')[2]
    return 0


def get_idle(seconds) -> str:
    """ calc idle time """
    return time.strftime("%H:%M:%S", time.gmtime(seconds))


def get_filesize(filename) -> int:
    """ get filesize in bytes """
    for file in filename, f'{glrootpath}{filename}':
        try:
            return os.path.getsize(file)
        except OSError:
            pass
    return 0


def get_geocode(client, userip, shown_err) -> list:
    """ get geoip2 country code for ip """
    iso_code = "xX"
    theme = Theme()
    if DEBUG > 0:
        for prefix in ['127.', '10.', '172.16.1', '172.16.2', '172.16.3', '192.168.']:
            if userip.startswith(prefix):
                if DEBUG > 3:
                    print(f'DEBUG: geoip2 MATCH "{prefix}" in "{userip}"')
                return [client, 'DEBUG', shown_err]
    if GEOIP2_BUF.get(userip):
        iso_code = GEOIP2_BUF[userip]
    else:
        try:
            if DEBUG == 1:
                print(f'DEBUG: geoip2 "{userip}" not cached, GEOIP2_BUF="{GEOIP2_BUF}"')
            iso_code = client.country(userip).country.iso_code
            GEOIP2_BUF[userip] = iso_code
        except geoip2.errors.GeoIP2Error as err:
            # var shown_err makes sure we only show the error once
            if (err.__class__.__name__ in ['AddressNotFoundError', 'OutOfQueriesError']) and shown_err == 0:
                shown_err = 1
                text = f"{Color('r,k')}Error: geoip2 {err.__class__.__name__} {err}"
                mcol = theme.max_col+8 if color else theme.max_col-8
                if len(text) + 8 > mcol:
                    text = text[:73] + ' ...'
                print("{0} {1:<{2}.{2}} {0}".format(Theme.vchar, text, mcol))
                print(theme.footer)
                print(f"{Esc('2F')}", end="")
                time.sleep(2.5)
    return [client, iso_code, shown_err]


def conv_speed(speed) -> list:
    """ convert and format speed """
    if speed > (THRESHOLD * THRESHOLD):
        return [float('{:7.2f}'.format(speed / 1024**2)), 'GiB/s']
    if speed > THRESHOLD:
        return [float('{:7.1f}'.format(speed / 1024)), 'MiB/s']
    return [float('{:7.0f}'.format(speed)), 'KiB/s']


def cli_stty_sane():
    """ default terminal settings, turn on echo """
    # restore orig tty settings (termios)
    tty.tcsetattr(sys.stdin, tty.TCSANOW, TTY_SETTINGS)


def cli_input(user_action, cli_refresh=0.5):
    """ read user input from stdin """
    # put terminal in mode 'cbreak' or 'raw' (no CR, disables ISIG)
    #   tty.setraw(), tty.setcbreak()
    #   https://github.com/python/cpython/blob/3.7/Lib/tty.py
    screen_redraw = 0
    key = ""
    tty.setraw(sys.stdin.fileno())
    if select.select([sys.stdin], [], [], cli_refresh) == ([sys.stdin], [], []):
        key = sys.stdin.buffer.raw.read(3).decode(sys.stdin.encoding)
        if key[:2].strip().isdigit():
            user_action = 1
        # [vV], ENTER, SPACE, RIGHT
        elif ((key in ['v', 'V', '\r', '\x13', '\N{SPACE}']) or
              (user_action == 0 and key == '\N{ESC}[C')):
            key = 1
            user_action = 1
        elif key in ['k', 'K']:
            user_action = 2
            screen_redraw = 1
        elif key in ['h', 'H', '?']:
            user_action = 3
            screen_redraw = 1
        # [nN], RIGHT
        elif key in ['n', 'N', '\N{ESC}[C']:
            user_action = 4
        # [pP], LEFT
        elif key in ['p', 'P', '\N{ESC}[D']:
            user_action = 5
        elif CLI_SEARCH and key == '/':
            user_action = 7
        # UP
        elif key == '\N{ESC}[A':
            user_action = 10
        # DOWN
        elif key == '\N{ESC}[B':
            user_action = 11
        elif key in ['q', 'Q']:
            user_action = 9
        # HOME
        elif key == '\N{ESC}[1':
            user_action = 12
        # END
        elif key == '\N{ESC}[4':
            user_action = 13
        # PGUP
        elif key == '\N{ESC}[5':
            user_action = 14
        # PGDN
        elif key == '\N{ESC}[6':
            user_action = 15
        # ESC
        elif key == '\N{ESC}':
            if user_action == 0:
                user_action = 9
            elif user_action == 1:
                user_action = 6
        # CTRL-C
        elif key == '\x03':
            cli_sigint_handler(any, any)
    tty.tcsetattr(sys.stdin, tty.TCSANOW, TTY_SETTINGS)
    return dict(key=key, user_action=user_action, screen_redraw=screen_redraw)


def cli_dialog(title, text):
    """ show dialog box """
    theme = Theme()
    draw = Theme.draw
    upos = str(theme.lines - 4)
    lpos = str(int(theme.columns / 10)-3)
    rpad = 10
    width = 72 - rpad
    tpad = f"{' '*int(width/2-8)}"  # pad title
    print(f"{Esc(upos+'A')}{Color('k,w')}")
    print(f"{Esc(lpos+'C')}{draw['ul']}{draw['h']*(width)}{draw['ur']}")
    print(f"{Esc(lpos+'C')}{draw['v']}{' '*width}{draw['v']}")
    print(f"{Esc(lpos+'C')}{draw['v']}  {tpad}{Color('k,c')}   {title}   {Color('k,w')}{' '*(width-(len(tpad)+12))}{draw['v']}")
    for line in text.splitlines():
        print(f"{Esc(lpos+'C')}{draw['v']}{line}{' '*(width-len(line))}{draw['v']}")
    print(f"{Esc(lpos+'C')}{draw['dl']}{draw['h']*(width)}{draw['dr']}")
    print(f"{Style('r')}")


def cli_user_info(users, u_idx, user_cache=[]) -> list:
    """ show formatted user details from login and userfile """
    theme = Theme()
    mcol_title = theme.max_col+8 if color else theme.max_col
    i = 0
    print(theme.header)
    if theme.lines > 25:
        print(theme.spacer)
        i += 1

    print(theme.fmt_uinfo_title('Login', mcol_title))
    print(theme.spacer)
    if users[u_idx].get('procid'):
        ssl_flag = users[u_idx].get('ssl_flag')
        ssl_msg = User.tls_mode[ssl_flag] if ssl_flag in range(0, len(User.tls_mode)) else 'UNKNOWN'
        ip = users[u_idx].ip
        iso_code  = None
        try:
            iso_code = users[u_idx].iso_code
        except AttributeError:
            if u_idx in range(0, len(user_cache)) and users[u_idx].get('procid') == user_cache[u_idx].get('procid'):
                try:
                    iso_code = user_cache[u_idx].iso_code
                except AttributeError:
                    pass
        last_dl = '{:.1f}GB'.format(round(int(users[u_idx].get_bytes_txfer()) / 1024**3, 1))
        login_info = [
            f"Username: '{Style('b')}{users[u_idx].name}{Style('r')}' [{u_idx}/{len(users)-1}]",
            f"PID: {users[u_idx].get('procid')} SSL: {ssl_msg}",
            f"RHost: {users[u_idx].get('host')}",
            f"IP: {ip}{' ' + iso_code if iso_code else ''}",
            f"Tagline: {users[u_idx].get('tagline')}",
            f"Currentdir: {users[u_idx].get('currentdir')}",
            f"Status: {users[u_idx].get('status')}",
            f"Last DL: {last_dl}",
            " "
        ]
        mcol = theme.max_col+4 if color else theme.max_col-4
        for l in login_info:
            print(theme.fmt_uinfo_line(l, mcol))
            mcol = theme.max_col-4
        print(theme.separator)
        if theme.lines > 25:
            print(theme.spacer)
            i += 1
        r = get_userfile(users[u_idx].name)
        if r.get('status') == "Success":
            print(theme.fmt_uinfo_title('Userfile', mcol_title))
            print(theme.spacer)
            for key, val in r.get('result').items():
                text = f"{key}: {val}"
                if len(text) + 8 > theme.max_col:
                    text = text[:61] + ' ...'
                print(theme.fmt_uinfo_line(text, theme.max_col-4))
        else:
            text = f"User '{users[u_idx].name}' not found..."
            print("{0} {1:<{2}.{2}} {0}".format(Theme.vchar, text, theme.max_col))
            time.sleep(2)
        while 23 + i < theme.lines:
            print(theme.spacer)
            i += 1
        print(theme.footer)


def cli_uinfo_prompt(p_cnt):
    """ show prompt on userinfo screen """
    rpad = ' ' * (3 - p_cnt)
    progress = '{0:{fill}<{width}}'.format('', fill='.', width=int(p_cnt))
    prompt = f"> Press {Color('k,w')}n{Style('r')} for next login, " \
             f"{Color('k,w')}p{Style('r')} previous, " \
             f"{Color('k,w')}k{Style('r')} kill or " \
             f"{Color('k,w')}ESC{Style('r')} to go back"
    print("")
    print(f"{prompt}{progress}{rpad}", end="")
    print(f"{Esc('1F')}", end="")


def cli_action(user_action, key, screen_redraw, user_scroll, search_user):
    """ get user input and run action """
    theme = Theme()
    # action: userinfo
    if user_action == 1:
        users = []
        for user in get_users():
            users.append(set_stats(user))
        u_idx = p_cnt = 0
        # set u_idx from shortcut keys 0-9, search_user or user_scroll
        if isinstance(key, str) and key.isdigit() and int(key) in range(0, len(users)):
            u_idx = int(key)
        elif CLI_SEARCH:
            i = 0
            for user in users:
                if search_user in user.name and i in range(0, len(users)):
                    u_idx = i
                    break
                i += 1
        if user_scroll:
            u_idx += user_scroll
        print(f"{Esc('2J')}{Esc('H')}", end="")
        # show user details and wait for user input
        cli_user_info(users, u_idx)
        input_result = kill_result = None
        while True:
            input_result = cli_input(user_action, 0.3)
            # uinfo: kill
            if input_result.get('user_action') == 2:
                if u_idx in range(0, len(users)):
                    u_name = users[int(u_idx)].get('username')
                    kill_result = kill_procid(u_name, users)
                    if kill_result.get('status') == "Success":
                        print("{0:<{1}.{1}}".format(f"{kill_result.get('status')}: Killed PID '{kill_result.get('procid')}' ...", theme.columns))
                        time.sleep(2)
                    else:
                        print("{0:<{1}.{1}}".format(f"{kill_result.get('error')}", theme.columns))
                        time.sleep(3)
                    print(f"{' ':<{theme.columns}}")
                print(f"{Esc('2J')}{Esc('H')}", end="")
                break
            # uinfo: next
            if input_result.get('user_action') == 4:
                u_idx = u_idx + 1 if (u_idx + 1 < len(users)) else 0
                print(f"{Esc('2J')}{Esc('H')}", end="")
                cli_user_info(get_users(), u_idx, users)
            # uinfo: prev
            elif input_result.get('user_action') == 5:
                u_idx = u_idx-1 if (u_idx-1 < len(users) and u_idx > 0) else 0
                print(f"{Esc('2J')}{Esc('H')}", end="")
                cli_user_info(get_users(), u_idx, users)
            # uinfo: help
            if input_result.get('user_action') == 3:
                cli_dialog("Help", HELP_TEXT)
                while not cli_input(user_action).get('key'):
                    time.sleep(0.1)
                print(f"{Esc('2J')}{Esc('H')}", end="")
                cli_user_info(get_users(), u_idx, users)
            # uinfo: back (ESC), quit
            if input_result.get('user_action') in [6, 9]:
                break
            p_cnt = 0 if p_cnt > 3 else p_cnt
            cli_uinfo_prompt(p_cnt)
            p_cnt += 1
        tty.tcsetattr(sys.stdin, tty.TCSANOW, TTY_SETTINGS)
        user_action = input_result.get('user_action')
        screen_redraw = 1
    # action: show help popup
    elif user_action ==  3:
        cli_dialog("Help", HELP_TEXT)
        while not cli_input(user_action, 0.3).get('key'):
            time.sleep(0.1)
        user_action = 0
        screen_redraw = 1
    # action: search user
    elif CLI_SEARCH and user_action == 7:
        prompt = f"> Search for username [ENTER]: {Style('bl')}_{Style('r')}"
        print("{0:<{1}.{1}}".format(prompt, theme.columns))
        stdin_string = ""
        c = 0
        while True:
            user_input = sys.stdin.read(1)
            # backspace
            if user_input in [ '\b', '\x08', '\x7f' ]:
                c -= 1 if c > 0 else 0
                stdin_string = stdin_string[:-1]
                print(f"{Esc('1A')}{Esc(str(c+len(prompt)-9)+'C')} ")
            # ENTER
            elif user_input == '\n':
                break
            else:
                stdin_string += user_input
                print(f"{Esc('1A')}{Esc(str(c+len(prompt)-9)+'C')}{user_input}")
                c += 1
        search_user = stdin_string
        user_action = 0
    # action: quit (ESC)
    elif user_action == 9:
        cli_sigint_handler(any, any)
        sys.exit(0)
    # action: scroll user list up (-1)
    elif user_action == 10:
        user_action = 0
        screen_redraw = 1
        user_scroll -= 1
    # action: scroll user list down (+1)
    elif user_action == 11:
        user_action = 0
        screen_redraw = 1
        user_scroll += 1
    # action: scroll to user list end (0)
    elif user_action == 12:
        user_action = 0
        screen_redraw = 1
        user_scroll = 0
    # action: scroll to user list end
    elif user_action == 13:
        user_action = 0
        screen_redraw = 1
        user_scroll = len(get_users())-1
    # action: scroll user list page up (-5)
    elif user_action == 14:
        user_action = 0
        screen_redraw = 1
        user_scroll -= 5
    # action: scroll user list page down  (+5)
    elif user_action == 15:
        user_action = 0
        screen_redraw = 1
        user_scroll += 5
    # handle any other key presses
    elif user_action == 0 and len(key) > 0:
        screen_redraw = 1
        text = "Invalid option ... press 'h' for help"
        print("{0:>2.2}{1:<{2}}".format(
            ' ', f"{Style('b')}{Color('r,k')}{text}{Style('r')}", theme.columns)
        )
        print(f"{Esc('2F')}", end="")
        time.sleep(1)
    else:
        user_action = 0
        screen_redraw = 1
    return [user_action, screen_redraw, user_scroll, search_user]


def get_userfile(u_name) -> dict:
    """ get fields from userfile """
    userfile_text = ""
    try:
        with open(f'{USERS_DIR}/{u_name}', 'r', encoding='utf-8', errors='ignore') as userfile:
            userfile_text = userfile.readlines()
    except FileNotFoundError:
        return dict(status="FileNotFound")
    if userfile_text == "":
        return dict(status="UserNotFound")
    u_fields = {}
    value = []
    prev = ""
    for line in userfile_text:
        for key in ['FLAGS', 'CREDITS', 'GROUP', 'IP']:
            if key in line:
                if key != prev:
                    value = []
                if line.startswith('CREDITS'):
                    value = 0
                    c = int((re.sub(r'^CREDITS [^0](\d+).*', r'\1', line)).strip())
                    if isinstance(c, int) and c > 0:
                        value = f"{round(int(c) / 1024**2)}GB"
                else:
                    value.append(line.strip().split(' ')[1])
                u_fields[key] = value
                prev = key
    return dict(status="Success", result=u_fields)


def kill_procid(u_name, users):
    """ kill user using procid """
    for user in users:
        if user.name == u_name:
            if os.popen(f"ps --no-headers -o comm -p {user.get('procid')}").read().strip() == 'glftpd':
                try:
                    procid = user.get('procid')
                    os.kill(int(procid), 15)
                    return dict(status="Success", procid=procid)
                except OSError as err:
                    return dict(status="NotRoot", procid=procid, error=err)
    return dict(status="NotFound")


def set_stats(user):
    """ adds user statistics to object and summed totals as class var """
    tstop_tv_sec = calendar.timegm(time.gmtime())
    tstop_tv_usec = datetime.datetime.now().microsecond
    user.speed = 0
    user.fmt_status = ""

    # get filename
    if len(user.get('status')) > 4 and not user.get('status')[4:].startswith('-'):
        user.filename = user.get('status')[5:]
    if _WITH_GEOIP and GEOIP2_ENABLE:
        try:
            (User.geoip2_client, user.iso_code, User.geoip2_shown_err) = get_geocode(User.geoip2_client, user.ip, User.geoip2_shown_err)
        except geoip2.errors.GeoIP2Error:
            user.iso_code = None
    # ul speed
    if user.get_traf_dir() == "Up":
        user.speed = abs(
            user.get_bytes_xfer() / 1024 / ((tstop_tv_sec - user.get('tstart_tv_sec')) +
            (tstop_tv_usec - user.get('tstart_tv_usec')) / 1000000)
        )
        if FLASK_MODE:
            flask.session['total_up_speed'] += user.speed
            flask.session['uploads'] += 1
        else:
            User.total_up_speed += user.speed
            User.uploads += 1
        user.pct = -1
        user.p_bar = '?->'
    # dn speed
    elif user.get_traf_dir() == "Dn":
        realfile = user.get('currentdir')
        user.filesize = get_filesize(realfile)
        if user.filesize < user.get_bytes_xfer():
            user.filesize = user.get_bytes_xfer()
        user.pct = abs(
            user.get_bytes_xfer() / user.filesize * 100
        )
        i = 15 * user.get_bytes_xfer() / user.filesize
        i = 15 if i > 15 else i
        user.p_bar = f"{'':x<{int(abs(i))}}"
        user.speed = abs(
           user.get_bytes_xfer() / 1024 / ((tstop_tv_sec - user.get('tstart_tv_sec')) +
                (tstop_tv_usec - user.get('tstart_tv_usec')) / 1000000)
        )
        if FLASK_MODE:
            flask.session['total_dn_speed'] += user.speed
            flask.session['downloads'] += 1
        else:
            User.total_dn_speed += user.speed
            User.downloads += 1
    # idle time
    else:
        user.p_bar = user.filename = ""
        user.pct = 0
        seconds = tstop_tv_sec - user.get('tstart_tv_sec')
        if seconds > IDLE_BARRIER:
            if FLASK_MODE:
                flask.session['idlers'] += 1
            else:
                User.idlers += 1
        else:
            if FLASK_MODE:
                flask.session['browsers'] += 1
            else:
                User.browsers += 1
        user.fmt_status = 'Idle: {:>8.8}'.format(get_idle(seconds))

    user.online = get_idle(tstop_tv_sec - user.get('login_time'))

    if user.get_traf_dir() in ["Up", "Dn"]:
        user.fmt_status = '{}:{:2.2s}{}{}'.format(user.get_traf_dir(), ' ', *conv_speed(user.speed))

    return user


def get_users() -> list:
    """ create list of user objects, from shm """ 
    try:
        memory = sysv_ipc.SharedMemory(KEY, flags=sysv_ipc.SHM_RDONLY, mode=0)
    except sysv_ipc.ExistentialError as exc:
        raise RuntimeError('No logged in users found') from exc
    buf = memory.read()

    # clear objects and class vars
    user = None
    users = []
    User.uploads = UPLOADS
    User.downloads = DOWNLOADS
    User.total = 0
    User.total_up_speed = TOTAL_UP_SPEED
    User.total_dn_speed = TOTAL_DN_SPEED
    User.total_speed = 0
    User.browsers = BROWSERS
    User.idlers = IDLERS

    for user_tuple in struct.iter_unpack(STRUCT_FORMAT, buf):
        if struct_ONLINE._make(user_tuple).procid:
            user = User(struct_ONLINE._make(user_tuple))
            users.append(user)

    # set totals
    User.onlineusers = len(users) if users else 0
    User.total = User.uploads + User.downloads
    User.total_speed = User.total_up_speed + User.total_dn_speed

    try:
        memory.detach()
    except (UnboundLocalError, sysv_ipc.Error):
        pass
    if _WITH_GEOIP and GEOIP2_ENABLE:
        GEOIP2_CLIENT.close()

    return users

def cli_mainloop():
    """ output users/totals to terminal """
    theme = Theme()
    u_idx = 0
    repeat = 0              # init screen drawing related vars
    user_action = 0         # 1=userinfo 2=killuser 3=help 4=next 5=prev 6=back 7=search 9=quit
    screen_redraw = 0       # 1=redraw theme.header
    user_scroll = 0         # up=+1 down-1
    user_max = 15           # show max users per screen (on 80x25)
    user_list_maxlines = user_list_next = user_list_page = 0
    search_user = ""

    print(f"{Esc('2J')}{Esc('H')}", end="")
    print(theme.header)
    while True:
        theme = Theme()
        signal.signal(signal.SIGINT, cli_sigint_handler)
        try:
            users = []
            for user in get_users():
                users.append(set_stats(user))
        except RuntimeError:
            text = f"No users logged in.. Press {Style('b')}CTRL-C{Style('rb')} to quit"
            print(f"{Esc('2J')}{Esc('H')}", end="")
            print(theme.header)
            print(theme.spacer)
            print("{0} {1:<{2}.{2}} {0}".format(Theme.vchar, text, theme.max_col+9))
            print(theme.spacer)
            print(theme.footer)
            print()
            print(f"{Esc('4F')}")
            time.sleep(1)
            continue
        if repeat > 0 and user_action == 0:
            # print vars for debugging, sleep to be able to actualy view them
            if DEBUG > 4:
                print(f'DEBUG: cli vars user_action={user_action} screen_redraw={screen_redraw}')
                time.sleep(2)
            if screen_redraw == 0:
                # go back up and clear x lines per user + totals + usage prompt
                u_cnt = (len(users) * 1) if users else 0
                l_up = str(u_cnt + 5 + 1)
                print(f"{Esc(l_up+'F')}")
                print(f"{Esc('0J')}{Esc('2F')}", end="")
            else:
                # erase screen, move cursor home and show header
                print(f"{Esc('2J')}{Esc('H')}", end="")
                print(f"{Esc('H')}", end="")
                print(theme.header)
                screen_redraw = 0

        # reset user idx for every repeat
        u_idx = 0

        # on max users, goto next screen
        if user_list_maxlines and not user_list_page and user_scroll > user_max:
            user_list_next = 1
            user_list_page = 1
        elif user_list_maxlines and user_scroll <= user_max:
            user_list_next = 0
            user_list_page = 0

        # prepare user list to show and split > maxlines
        if user_list_maxlines and user_list_next:
            show_users = users[user_max+1:]
            u_idx = user_max+1
        else:
            user_list_page = 0
            show_users = users[:user_scroll] + users[user_scroll:]

        # user list loop, output formatted online users
        for user in show_users:
            if user_action == 0:
                if CLI_SEARCH and search_user != "" and search_user not in user.name:
                    continue
                if not user.pct and not user.p_bar:
                    cli_pct = ''
                else:
                    cli_pct = f"{user.pct:>.0f}%"

                if theme.columns > 80:
                    cli_path= f"{user.get('currentdir').replace('/site', ''):<22.22}"
                else:
                    cli_path = f'{user.filename}'

                if user.get_traf_dir() == "Up":
                    cli_info = cli_path
                else:
                    cli_info = f'{cli_pct + " " if cli_pct else ""}{cli_path}'

                menu_selector = " "
                if user_scroll == u_idx:
                    menu_selector = f"{Color('k,w')} " if color else ">"

                col = len(theme.fill) - 62 if len(theme.fill) > 0 else 18
                print("{vchar}{ms}[{index:>2}] {username:16.16s}/{g_name:>10.10} {delimiter} {status:14.14s} {delimiter} {cli_info:{col}.{col}}{vchar}{rst}".format(
                    ms=menu_selector, vchar=Theme.vchar, delimiter=Theme.delimiter, username=user.name, g_name=user.group, index=u_idx, status=user.fmt_status,
                    cli_info=cli_info, col=col, rst=Style('r')
                ))

                u_idx += 1

            # max lines printed for screen size
            if user_action == 0 and u_idx + 9 > theme.lines:
                user_list_maxlines = 1
                if user_list_next == 0:
                    # show right side scroll indicator 'v'
                    print(f"{Esc('1A')}{Esc(str(theme.max_col+3)+'C')}{Color('m,k')}v")
                    break

        # fill screen, after last user (next page)
        if user_list_maxlines and user_list_next:
            i = 0
            while i + (len(users) - user_max) < theme.lines - 7:
                print(theme.spacer)
                i += 1

        # show totals
        if user_action == 0:
            total_up_speed, total_up_unit = conv_speed(User.total_up_speed)
            total_dn_speed, total_dn_unit = conv_speed(User.total_dn_speed)
            total_speed, total_unit = conv_speed(User.total_speed)
            i = 0
            # fill screen; after last user, add 8 lines for totals + prompt
            while u_idx + 8 + i < theme.lines:
                print(theme.spacer)
                i += 1
            print(theme.separator)
            print("{vchar} Up: {uploads:>2} / {total_up_speed:6}{up_unit:5} {delimiter} Dn: {downloads:>2} / {total_dn_speed:6}{dn_unit:5} {delimiter} Total: {total:>2} / {total_speed:6}{total_unit:5} {fill}{vchar}".format(
                vchar=Theme.vchar, delimiter=Theme.delimiter,
                uploads=users[0].uploads, total_up_speed=total_up_speed, up_unit=total_up_unit,
                downloads=users[0].downloads, total_dn_speed=total_dn_speed, dn_unit=total_dn_unit,
                total=users[0].total, total_speed=total_speed, total_unit=total_unit, fill=f'{" "*(theme.columns-80)}'
            ))
            print("{vchar} Currently {onlineusers:>3}{rb} of {maxusers:>3} users are online... {space:19} {curtime} {fill}{vchar}".format(
                vchar=Theme.vchar, space=' ', onlineusers=users[0].onlineusers, rb=Style('rb'), maxusers=TOTALUSERS, fill=f'{" "*(theme.columns-80)}',
                curtime = datetime.datetime.now().strftime("%T")
            ))
            print(theme.footer)
            print()

        # show usage text and user input prompt
        if user_action == 0:
            prompt = f"> View user details with {Color('k,w')}v{Style('r')} " \
                     f"or press {Color('k,w')}h{Style('r')} key for help. " \
                     f"Press {Style('b')}CTRL-C{Style('rb')} to quit"
            print(prompt, end="")
            print(f"{Esc('1A')}")

        # handle keyboard input
        input_result = cli_input(user_action, REFRESH)
        [user_action, screen_redraw, user_scroll, search_user] = cli_action(
            **input_result,
            user_scroll=user_scroll,
            search_user=search_user
        )

        #if _WITH_GEOIP and GEOIP2_ENABLE:
        #    time.sleep(2)
        #if user_scroll == 1:
        #    time.sleep(2)

        # dont scroll outside bounds
        if user_scroll not in range(0, len(users)):
            if user_list_maxlines or not user_list_next:
                # top
                if user_scroll <= 0:
                    user_scroll = 0
                # bottom
                elif user_scroll >= len(users):
                    user_scroll = len(users) - 1
                    continue

        repeat += 1


def cli_sigint_handler(signal_received, frame):
    # pylint: disable=unused-argument
    """ handle ctrl-c """
    theme = Theme()
    tty.tcsetattr(sys.stdin, tty.TCSANOW, TTY_SETTINGS)
    print(f"{Esc('1E')}")
    print(f'\n{"Exiting spy.py...":<{theme.columns}}\n')
    print(Style('r'), end="")
    if _WITH_GEOIP and GEOIP2_ENABLE:
        GEOIP2_CLIENT.close()
    sys.exit(0)


def format_html() -> str:
    """ return string with users/totals as html """
    html = "<h3>SPY.PY</h3><br>\n"
    users = []
    try:
        for user in get_users():
            users.append(set_stats(user))
    except RuntimeError:
        return "No logged in users users found"
    for u in users:
        html += f"{u.name}/{u.group}<br>\n"
        html += f"tagline: {u.get('tagline')}<br>\n"
        html += f"host: ({u.get('host')})<br>\n"
        html += f"status: {u.fmt_status}<br><br>\n\n"
    html += "<hr><br>\n"
    html += f"currently {str(User.onlineusers)} users of {MAXUSERS} users online<br>\n"
    html += f"up: {User.uploads} {conv_speed(User.total_up_speed)}, "
    html += f"down: {User.downloads} {conv_speed(User.total_dn_speed)}, "
    html += f"total: {User.total} {conv_speed(User.total_speed)}<br>\n"
    html += f"{str(User.browsers)} browser(s), {str(User.idlers)} idler(s)<br>\n"
    return html


def create_app() -> object:
    """ create flask app with routes """
    if _WITH_FLASK and FLASK_MODE == 1:
        tmpl_path = os.path.join(SCRIPT_DIR, 'webspy/templates/')
        static_path = os.path.join(SCRIPT_DIR, 'webspy/static/')
        app = flask.Flask(__name__, template_folder=tmpl_path, static_folder=static_path, static_url_path='/static')
        app.secret_key = 'SECRET_KEY'
        if FLASK_PROXY:
            app.wsgi_app = ProxyFix(
                app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
            )
        @app.route('/')
        def index():
            return flask.redirect('spy')
        @app.route('/favicon.ico')
        def favicon():
            return ''
        @app.route('/spy.js')
        def spy_js():
            response = flask.make_response(flask.render_template("js/spy.js"))
            response.headers['Content-Type'] = "text/javascript"
            return response
        @app.route('/users', defaults={'route': 'users'})
        @app.route('/totals', defaults={'route': 'totals'})
        @app.route('/spy', defaults={'route': 'spy'})
        def webspy(route):
            users = []
            err = None
            total_up_speed = total_dn_speed = total_speed = 0
            total_up_unit = total_dn_unit = total_unit = ""
            for i in ['idlers', 'browsers', 'uploads', 'downloads', 'total', 'total_up_speed', 'total_dn_speed', 'total_speed', 'onlineusers']:
                flask.session[i] = 0
            try:
                for user in get_users():
                    users.append(set_stats(user))
                flask.session['onlineusers'] = len(users)
                total_up_speed, total_up_unit = conv_speed(flask.session.get('total_up_speed'))
                total_dn_speed, total_dn_unit = conv_speed(flask.session.get('total_dn_speed'))
                total_speed, total_unit = conv_speed(flask.session.get('total_speed'))
            except RuntimeError:
                users = None
                err = "No logged in users users found"
            sort_attr = flask.request.args.get('sort_attr', default='username', type=str)
            if flask.request.args.get('sort_attr') == '' and bool(flask.request.args.get('sort_rev')):
                sort_attr = 'name'
            return flask.render_template(
                # f'{flask.request.path}.html',
                f'{route}.html',
                users = users,
                glftpd_version = GL_VER,
                spy_version = SPY_VERSTR,
                totalusers = TOTALUSERS,
                sort_attr = sort_attr,
                sort_rev = flask.request.args.get('sort_rev', default=False, type=bool),
                uniq_attr = flask.request.args.get('uniq_attr', default=None, type=str),
                search = flask.request.args.get('search', default=None, type=str),
                uploads = flask.session.get('uploads'),
                downloads = flask.session.get('downloads'),
                total = flask.session.get('uploads') +  flask.session.get('downloads'),
                total_up_speed = total_up_speed,
                total_dn_speed = total_dn_speed,
                total_speed = total_speed,
                total_up_unit = total_up_unit,
                total_dn_unit = total_dn_unit,
                total_unit = total_unit,
                idlers = flask.session.get('idlers'),
                browsers = flask.session.get('browsers'),
                onlineusers = flask.session.get('onlineusers'),
                curdate = datetime.datetime.now().strftime("%F %T"),
                error = err
            )
        @app.route('/user/<username>')
        def user(username):
            r = get_userfile(username)
            status = r.get('status')
            if status == "Success":
                return r.get('result'), 200
            elif status == "FileNotFound":
                return [f"{status}"], 500
            elif status == "UserNotFound":
                return [f"{status}"], 404
            return ["Unknown"], 500
        @app.route('/kick/<username>')
        def kick(username):
            r = kill_procid(username, get_users())
            status = r.get('status')
            print(status)
            if status == "Success":
                return [f"{status}: killed user"], 200
            if status == "NotFound":
                return [f"{status}"], 404
            if status == "NotRoot":
                return [f"{r.get('error')}"], 500
            return ["Unknown"], 500
        @app.route('/html')
        def html():
            return format_html()
        return app


# main
#######

def main():
    """ start flask, http.server or cli (default) """
    if APP:
        APP.run(**FLASK_OPTIONS)
    elif _WITH_HTTPD and HTTPD_MODE == 1:
        print('Starting http server thread', HTTPD_OPTIONS)
        t = TCPServerThread()
        t.daemon = True
        try:
            t.start()
            t.join()
        except (KeyboardInterrupt, SystemExit):
            print('HTTPD mode exiting...')
            raise
        finally:
            sys.exit(0)
    else:
        cli_mainloop()

if _WITH_FLASK and FLASK_MODE == 1:
    APP = create_app()
else:
    APP = None
if __name__ == "__main__":
    main()

# fuquallkthnxbye.
