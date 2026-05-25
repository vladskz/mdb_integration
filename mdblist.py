# -*- coding: utf-8 -*-
"""
mdblist.py — Self-contained MDBList plugin module for Kodi addons
=================================================================
Version: 3.0.0

DROP-IN INTEGRATION (3 steps)
------------------------------
1. Copy this file to:  resources/lib/mdblist.py

2. Add to your settings.xml  (see SETTINGS SNIPPET below).

3. In your addon.py router, call handle_mdblist_action(params) for
   any action that starts with 'mdblist_':

       from mdblist import handle_mdblist_action, MDBLIST_ACTIONS

       # inside your router:
       elif action in MDBLIST_ACTIONS:
           handle_mdblist_action(params, HANDLE, BASE_URL, ADDON)

   That's it. Every menu, list, watchlist, and Up Next view is handled
   internally. No other changes to your addon.py are needed.

SETTINGS SNIPPET — paste into your <settings> block
-----------------------------------------------------
    <category label="MDBList">
        <setting id="mdblist_api"
                 type="text"
                 label="MDBList API Key"
                 default=""
                 help="Get your free key at mdblist.com/preferences" />
        <setting id="mdblist_client_id"
                 type="text"
                 label="OAuth Client ID"
                 default=""
                 help="Register a Device Code app at mdblist.com/developer" />
        <setting id="mdblist_bearer_token"
                 type="text"
                 label="Bearer Token (auto-filled)"
                 default="" />
        <setting id="mdblist_refresh_token"
                 type="text"
                 label="Refresh Token (auto-filled)"
                 default="" />
    </category>

OPTIONAL SETTINGS (add if you want user-configurable behaviour)
---------------------------------------------------------------
    <setting id="page_limit"       type="number" label="Items Per Page"          default="20" />
    <setting id="new_episode_days" type="number" label="NEW badge threshold days" default="7"  />
    <setting id="tmdb_api"         type="text"   label="TMDB API Key"             default=""   />
    <setting id="tmdb_language"    type="text"   label="TMDB Language"            default="en-US" />

DEPENDENCY — add to addon.xml
------------------------------
    <import addon="script.module.requests" version="2.22.0"/>

AUTHENTICATION
--------------
  Read  (free)      → API key via ?apikey=
  Write (supporter) → Bearer token via OAuth2 Device Code flow
                      Triggered by the "Connect MDBList Account" menu item.
                      Token is saved/refreshed automatically.

PAGINATION
----------
  List items and watchlist use cursor-based pagination (preferred by the API).
  Pass next_cursor from one page into the next request; no cursor = first page.
  Popular lists and Up Next use offset-based pagination (API limitation).
"""

import sys
import time
import urllib.parse
from datetime import datetime, timezone, timedelta

import requests
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmc

# ---------------------------------------------------------------------------
# All action names this module owns.
# Use this set in your router:  elif action in MDBLIST_ACTIONS: ...
# ---------------------------------------------------------------------------
MDBLIST_ACTIONS = {
    'mdblist_connect',
    'mdblist_menu',
    'mdblist_my',
    'mdblist_popular',
    'mdblist_liked',
    'mdblist_search',
    'mdblist_view_list',
    'mdblist_watchlist_menu',
    'mdblist_watchlist_items',
    'mdblist_watchlist_add',
    'mdblist_watchlist_remove',
    'mdblist_upnext',
}

BASE_URL_API = 'https://api.mdblist.com/'

# ---------------------------------------------------------------------------
# Internal state — populated by handle_mdblist_action()
# ---------------------------------------------------------------------------
_HANDLE   = None
_BASE_URL = None   # plugin:// base URL of the host addon
_ADDON    = None   # xbmcaddon.Addon instance of the host addon


def _build_url(query):
    return _BASE_URL + '?' + urllib.parse.urlencode(query)


# ---------------------------------------------------------------------------
# Addon settings helpers
# ---------------------------------------------------------------------------

def _setting(key, fallback=''):
    try:
        return (_ADDON.getSetting(key) or fallback).strip()
    except Exception:
        return fallback


def _api_key():
    return _setting('mdblist_api')


def _bearer_token():
    return _setting('mdblist_bearer_token')


def _client_id():
    return _setting('mdblist_client_id')


def _save_setting(key, value):
    try:
        _ADDON.setSetting(key, value)
    except Exception as e:
        xbmc.log(f'[mdblist] setSetting failed for {key}: {e}', xbmc.LOGWARNING)


def _save_bearer(token):
    _save_setting('mdblist_bearer_token', token)
    xbmc.log('[mdblist] Bearer token saved.', xbmc.LOGINFO)


def _page_limit():
    try:
        return int(_setting('page_limit', '20'))
    except ValueError:
        return 20


def _new_episode_days():
    try:
        v = max(1, min(int(_setting('new_episode_days', '7')), 30))
        return v
    except (ValueError, TypeError):
        return 7


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _notify(title, msg, icon=xbmcgui.NOTIFICATION_INFO, ms=4000):
    xbmcgui.Dialog().notification(title, msg, icon, ms)


# ---------------------------------------------------------------------------
# Low-level HTTP
# ---------------------------------------------------------------------------

def _get(path, params=None):
    key = _api_key()
    if not key:
        _notify('MDBList', 'API key not set — check addon settings.',
                xbmcgui.NOTIFICATION_WARNING)
        return None
    p = {'apikey': key}
    if params:
        p.update(params)
    try:
        r = requests.get(BASE_URL_API + path, params=p, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        xbmc.log(f'[mdblist] HTTP {e.response.status_code} on GET /{path}', xbmc.LOGERROR)
        _notify('MDBList Error', f'Server returned {e.response.status_code}',
                xbmcgui.NOTIFICATION_ERROR)
    except Exception as e:
        xbmc.log(f'[mdblist] Exception on GET /{path}: {e}', xbmc.LOGERROR)
    return None


def _post(path, payload):
    token = _bearer_token()
    if not token:
        if not refresh_bearer_token():
            _notify('MDBList',
                    'Write access needs OAuth login. '
                    'Use "Connect MDBList Account" in the menu.',
                    xbmcgui.NOTIFICATION_WARNING, 6000)
            return None
        token = _bearer_token()
    try:
        r = requests.post(
            BASE_URL_API + path,
            headers={'Authorization': f'Bearer {token}'},
            json=payload,
            timeout=10
        )
        if r.status_code == 401:
            _save_bearer('')
            _notify('MDBList',
                    'Session expired. Please reconnect your account.',
                    xbmcgui.NOTIFICATION_WARNING, 6000)
            return None
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        xbmc.log(f'[mdblist] HTTP {e.response.status_code} on POST /{path}', xbmc.LOGERROR)
    except Exception as e:
        xbmc.log(f'[mdblist] Exception on POST /{path}: {e}', xbmc.LOGERROR)
    return None


# ---------------------------------------------------------------------------
# OAuth — Device Code flow
# ---------------------------------------------------------------------------

def start_device_auth():
    """
    Run the Device Code OAuth flow (blocking, shows Kodi dialogs).
    Saves Bearer + refresh tokens to addon settings on success.
    Returns True on success.
    """
    client_id = _client_id()
    if not client_id:
        xbmcgui.Dialog().ok(
            'MDBList OAuth',
            'No Client ID configured.\n'
            'Register a Device Code app at mdblist.com/developer\n'
            'and enter the Client ID in addon settings.'
        )
        return False

    try:
        r = requests.post(
            BASE_URL_API + 'oauth/device-authorization/',
            data={'client_id': client_id, 'scope': 'write'},
            timeout=10
        )
        r.raise_for_status()
        resp = r.json()
    except Exception as e:
        xbmc.log(f'[mdblist] Device auth request failed: {e}', xbmc.LOGERROR)
        _notify('MDBList', 'Could not start authentication.', xbmcgui.NOTIFICATION_ERROR)
        return False

    device_code = resp.get('device_code')
    user_code   = resp.get('user_code')
    verify_url  = resp.get('verification_uri', 'https://mdblist.com/oauth/device/')
    expires_in  = int(resp.get('expires_in', 300))
    interval    = int(resp.get('interval', 5))

    xbmcgui.Dialog().ok(
        'Connect MDBList Account',
        f'1. Open [B]{verify_url}[/B] on any device\n'
        f'2. Enter code: [B]{user_code}[/B]\n'
        f'3. Press OK here — the addon will wait for approval.\n\n'
        f'Code expires in {expires_in // 60} minutes.'
    )

    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        try:
            pr = requests.post(
                BASE_URL_API + 'oauth/token/',
                data={
                    'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                    'device_code': device_code,
                    'client_id': client_id,
                },
                timeout=10
            )
            poll = pr.json()
        except Exception as e:
            xbmc.log(f'[mdblist] Token poll error: {e}', xbmc.LOGWARNING)
            continue

        if 'access_token' in poll:
            _save_bearer(poll['access_token'])
            if poll.get('refresh_token'):
                _save_setting('mdblist_refresh_token', poll['refresh_token'])
            _notify('MDBList', 'Account connected! Watchlist write access enabled.')
            return True

        error = poll.get('error', '')
        if error == 'authorization_pending':
            continue
        elif error == 'slow_down':
            interval += 5
        elif error in ('access_denied', 'expired_token'):
            xbmc.log(f'[mdblist] Device auth terminal error: {error}', xbmc.LOGWARNING)
            _notify('MDBList', f'Authentication failed: {error}', xbmcgui.NOTIFICATION_ERROR)
            return False

    _notify('MDBList', 'Authentication timed out. Please try again.',
            xbmcgui.NOTIFICATION_WARNING)
    return False


def refresh_bearer_token():
    """Silently refresh the Bearer token. Returns True on success."""
    client_id     = _client_id()
    refresh_token = _setting('mdblist_refresh_token')
    if not client_id or not refresh_token:
        return False
    try:
        r = requests.post(
            BASE_URL_API + 'oauth/token/',
            data={
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
                'client_id': client_id,
            },
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        if 'access_token' in data:
            _save_bearer(data['access_token'])
            if data.get('refresh_token'):
                _save_setting('mdblist_refresh_token', data['refresh_token'])
            xbmc.log('[mdblist] Bearer token refreshed silently.', xbmc.LOGINFO)
            return True
    except Exception as e:
        xbmc.log(f'[mdblist] Token refresh failed: {e}', xbmc.LOGWARNING)
    return False


def is_authenticated():
    """Returns True if a Bearer token is stored (write access available)."""
    return bool(_bearer_token())


# ---------------------------------------------------------------------------
# API — Lists
# ---------------------------------------------------------------------------

def fetch_user_lists():
    data = _get('lists/user')
    return data if isinstance(data, list) else []


def fetch_top_lists(offset=0, limit=20):
    data = _get('lists/top', {'limit': limit, 'offset': offset})
    if data is None:
        return []
    return data if isinstance(data, list) else data.get('lists', [])


def fetch_liked_lists():
    data = _get('lists/liked')
    if data is None:
        return []
    return data if isinstance(data, list) else data.get('lists', [])


def search_lists(query, offset=0, limit=20):
    if not query:
        return []
    data = _get('lists/search', {'query': query, 'limit': limit, 'offset': offset})
    if data is None:
        return []
    return data if isinstance(data, list) else data.get('lists', [])


def fetch_list_items(list_id, cursor=None, limit=20):
    """Fetch one page of list items using cursor pagination.

    Returns (items, next_cursor). Pass next_cursor back on the next call to
    get the following page; None means you are on the last page.
    """
    params = {'limit': limit}
    if cursor:
        params['cursor'] = cursor
    data = _get(f'lists/{list_id}/items', params)
    if data is None:
        return [], None
    if isinstance(data, list):
        return data, None
    items = data.get('movies', []) + data.get('shows', [])
    next_cursor = (data.get('pagination') or {}).get('next_cursor')
    return items, next_cursor


# ---------------------------------------------------------------------------
# API — Watchlist
# ---------------------------------------------------------------------------

def fetch_watchlist(mediatype=None, cursor=None, limit=20):
    """Fetch one page of watchlist items using cursor pagination.

    Returns (items, next_cursor). Pass next_cursor back on the next call to
    get the following page; None means you are on the last page.
    """
    params = {'limit': limit}
    if cursor:
        params['cursor'] = cursor
    path = (
        f'watchlist/items/{mediatype}'
        if mediatype in ('movie', 'show')
        else 'watchlist/items'
    )
    data = _get(path, params)
    if data is None:
        return [], None
    if isinstance(data, list):
        return data, None
    if mediatype == 'movie':
        items = data.get('movies', [])
    elif mediatype == 'show':
        items = data.get('shows', [])
    else:
        items = data.get('movies', []) + data.get('shows', [])
    next_cursor = (data.get('pagination') or {}).get('next_cursor')
    return items, next_cursor


def _watchlist_payload(imdb_id, tmdb_id, mediatype):
    ids = {}
    if imdb_id:
        ids['imdb'] = imdb_id
    if tmdb_id:
        try:
            ids['tmdb'] = int(tmdb_id)
        except (ValueError, TypeError):
            pass
    entry = {'ids': ids}
    if str(mediatype).lower() in ('show', 'tv', 'series'):
        return {'shows': [entry]}
    return {'movies': [entry]}


def watchlist_add(imdb_id=None, tmdb_id=None, mediatype='movie'):
    if not imdb_id and not tmdb_id:
        return False
    result = _post('watchlist/items/add', _watchlist_payload(imdb_id, tmdb_id, mediatype))
    if result is not None:
        _notify('Watchlist', 'Added to watchlist.' if result.get('added', 0) else 'Already in watchlist.')
        return True
    return False


def watchlist_remove(imdb_id=None, tmdb_id=None, mediatype='movie'):
    if not imdb_id and not tmdb_id:
        return False
    result = _post('watchlist/items/remove', _watchlist_payload(imdb_id, tmdb_id, mediatype))
    if result is not None:
        removed = result.get('removed', {})
        count = (
            removed.get('movies', 0) + removed.get('shows', 0)
            if isinstance(removed, dict) else int(removed)
        )
        _notify('Watchlist', 'Removed from watchlist.' if count else 'Item was not in watchlist.')
        return True
    return False


# ---------------------------------------------------------------------------
# API — Up Next
# ---------------------------------------------------------------------------

def fetch_upnext(offset=0, limit=20):
    """Fetch one page of Up Next items using offset pagination.

    The API does not yet support cursor pagination for this endpoint.
    Returns (items, has_more).
    """
    data = _get('upnext', {'limit': limit, 'offset': offset, 'hide_unreleased': 'true'})
    if data is None:
        return [], False
    if isinstance(data, dict):
        return data.get('items', []), data.get('has_more', False)
    if isinstance(data, list):
        return data, False
    return [], False


# Backwards-compat alias
fetch_mdblist_upnext = fetch_upnext


# ---------------------------------------------------------------------------
# TMDB metadata
# ---------------------------------------------------------------------------

def _get_tmdb_metadata(media_type, tmdb_id, season=None, episode=None):
    """Fetch TMDB metadata. Returns (data, overview, poster_url, fanart_url)."""
    if not tmdb_id or str(tmdb_id).lower() == 'none':
        return {}, '', '', ''

    api_key  = _setting('tmdb_api', 'b8eabaf5608b88d0298aa189dd90bf00')
    language = _setting('tmdb_language', 'en-US')
    media_type = str(media_type).lower()

    if media_type == 'episode' and season and episode:
        url = f'https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}'
    elif media_type in ('show', 'tv', 'series', 'season'):
        url = f'https://api.themoviedb.org/3/tv/{tmdb_id}'
    else:
        url = f'https://api.themoviedb.org/3/movie/{tmdb_id}'

    try:
        r = requests.get(url, params={'api_key': api_key, 'language': language}, timeout=7)
        if r.status_code == 200:
            d = r.json()
            poster = f"https://image.tmdb.org/t/p/w500{d['poster_path']}"    if d.get('poster_path')   else ''
            fanart = f"https://image.tmdb.org/t/p/w1280{d['backdrop_path']}" if d.get('backdrop_path') else ''
            if d.get('still_path') and not poster:
                poster = f"https://image.tmdb.org/t/p/w500{d['still_path']}"
            return d, d.get('overview', ''), poster, fanart
    except Exception as e:
        xbmc.log(f'[mdblist] TMDB fetch error: {e}', xbmc.LOGWARNING)

    return {}, '', '', ''


# ---------------------------------------------------------------------------
# Kodi UI — shared helpers
# ---------------------------------------------------------------------------

def _end(succeeded=True):
    xbmcplugin.endOfDirectory(_HANDLE, succeeded=succeeded)


def _add_dir(url, li, is_folder=True):
    xbmcplugin.addDirectoryItem(_HANDLE, url, li, is_folder)


def _empty(label):
    _add_dir(_build_url({}), xbmcgui.ListItem(label=label), False)


def _build_list_item(item, mediatype_override=None):
    """Build a Kodi ListItem from an MDBList item dict. Returns (url, li, is_folder)."""
    raw_title = item.get('title', '')
    raw_year  = item.get('year', '')
    tmdb_id   = item.get('tmdbid') or item.get('tmdb_id') or item.get('show_tmdbid') or item.get('id', '')
    imdb_id   = item.get('imdb_id') or item.get('imdbid', '')
    mediatype = mediatype_override or item.get('mediatype', 'movie')

    tmdb_data, plot, poster, fanart = _get_tmdb_metadata(mediatype, tmdb_id)

    display_title = tmdb_data.get('title') or tmdb_data.get('name') or raw_title
    release_date  = tmdb_data.get('release_date') or tmdb_data.get('first_air_date') or ''
    year_str      = release_date.split('-')[0] if release_date else str(raw_year)
    year          = int(year_str) if year_str.isdigit() else 0

    if year:
        display_title = f'{display_title} ({year})'

    is_show      = str(mediatype).lower() in ('show', 'tv', 'series')
    kodi_type    = 'tvshow' if is_show else 'movie'

    li = xbmcgui.ListItem(label=display_title)
    li.setInfo('video', {'title': display_title, 'year': year, 'plot': plot, 'mediatype': kodi_type})
    li.setProperty('IsPlayable', 'false')
    li.setArt({'poster': poster, 'thumb': poster, 'icon': poster, 'fanart': fanart})

    li.addContextMenuItems([
        ('Add to Watchlist', 'RunPlugin(%s)' % _build_url({
            'action': 'mdblist_watchlist_add',
            'tmdb_id': tmdb_id, 'imdb_id': imdb_id, 'mediatype': mediatype
        })),
        ('Remove from Watchlist', 'RunPlugin(%s)' % _build_url({
            'action': 'mdblist_watchlist_remove',
            'tmdb_id': tmdb_id, 'imdb_id': imdb_id, 'mediatype': mediatype
        })),
    ])

    play_url = _build_url({'action': 'play', 'tmdb_id': tmdb_id, 'mediatype': mediatype})
    return play_url, li, False


# ---------------------------------------------------------------------------
# Kodi UI — views
# ---------------------------------------------------------------------------

def _view_menu():
    """Top-level MDBList submenu."""
    xbmcplugin.setContent(_HANDLE, 'files')

    auth_label = (
        '✅  MDBList Account Connected'
        if is_authenticated()
        else '🔑  Connect MDBList Account'
    )

    sections = [
        (auth_label,         'mdblist_connect',       'DefaultAddonService.png', False),
        ('⭐  My Watchlist',  'mdblist_watchlist_menu','DefaultVideoPlaylists.png', True),
        ('📺  Up Next',       'mdblist_upnext',        'DefaultTVShows.png',        True),
        ('📋  My Lists',      'mdblist_my',            'DefaultVideoPlaylists.png', True),
        ('🔥  Popular Lists', 'mdblist_popular',       'DefaultMovies.png',         True),
        ('❤️  Liked Lists',   'mdblist_liked',         'DefaultFavourites.png',     True),
        ('🔍  Search Lists',  'mdblist_search',        'DefaultAddonsSearch.png',   True),
    ]

    for label, action, icon, is_folder in sections:
        li = xbmcgui.ListItem(label=label)
        li.setArt({'icon': icon})
        _add_dir(_build_url({'action': action}), li, is_folder)

    _end()


def _render_list_folders(lists, empty_label='[No Lists Found]'):
    if not lists:
        _empty(empty_label)
    else:
        for lst in lists:
            name    = lst.get('name', 'Unnamed List')
            list_id = lst.get('id')
            parts   = []
            if lst.get('items'): parts.append(f'{lst["items"]} items')
            if lst.get('likes'): parts.append(f'♥ {lst["likes"]}')
            if lst.get('user_name'): parts.append(f'by {lst["user_name"]}')
            suffix = f'  [{", ".join(parts)}]' if parts else ''
            li = xbmcgui.ListItem(label=f'{name}{suffix}')
            li.getVideoInfoTag().setTitle(name)
            _add_dir(_build_url({'action': 'mdblist_view_list', 'list_id': str(list_id)}), li, True)
    _end()


def _view_my_lists():
    xbmcplugin.setContent(_HANDLE, 'files')
    _render_list_folders(fetch_user_lists())


def _view_popular(offset=0):
    xbmcplugin.setContent(_HANDLE, 'files')
    limit = _page_limit()
    lists = fetch_top_lists(offset=int(offset), limit=limit)
    if not lists:
        _empty('[No Popular Lists Found]')
    else:
        for lst in lists:
            name    = lst.get('name', 'Unnamed List')
            list_id = lst.get('id')
            li = xbmcgui.ListItem(label=name)
            _add_dir(_build_url({'action': 'mdblist_view_list', 'list_id': str(list_id)}), li, True)
        if len(lists) == limit:
            next_li = xbmcgui.ListItem(label='Next Page')
            _add_dir(_build_url({'action': 'mdblist_popular', 'offset': int(offset) + limit}), next_li, True)
    _end()


def _view_liked():
    xbmcplugin.setContent(_HANDLE, 'files')
    _render_list_folders(fetch_liked_lists(), '[No Liked Lists Found]')


def _view_search(query=None):
    xbmcplugin.setContent(_HANDLE, 'files')
    if not query:
        query = xbmcgui.Dialog().input('Search MDBList Lists', type=xbmcgui.INPUT_ALPHANUM)
    if not query:
        _end()
        return
    _render_list_folders(search_lists(query), f'[No results for "{query}"]')


def _view_list_contents(list_id, cursor=None):
    """Render one page of list items, threading cursor through the Next Page URL."""
    xbmcplugin.setContent(_HANDLE, 'videos')
    limit = _page_limit()
    items, next_cursor = fetch_list_items(list_id, cursor=cursor or None, limit=limit)
    if not items:
        _empty('[No Items Found]')
        _end()
        return
    for item in items:
        url, li, is_folder = _build_list_item(item)
        _add_dir(url, li, is_folder)
    if next_cursor:
        next_li = xbmcgui.ListItem(label='Next Page')
        _add_dir(
            _build_url({'action': 'mdblist_view_list', 'list_id': list_id, 'cursor': next_cursor}),
            next_li, True,
        )
    _end()


def _view_watchlist_menu():
    xbmcplugin.setContent(_HANDLE, 'videos')
    for label, mediatype, icon in [
        ('Movies', 'movie', 'DefaultMovies.png'),
        ('Shows',  'show',  'DefaultTVShows.png'),
    ]:
        li = xbmcgui.ListItem(label=label)
        li.setArt({'icon': icon})
        _add_dir(_build_url({'action': 'mdblist_watchlist_items', 'mediatype': mediatype}), li, True)
    _end()


def _view_watchlist_items(mediatype, cursor=None):
    """Render one page of watchlist items, threading cursor through the Next Page URL."""
    kodi_content = 'movies' if mediatype == 'movie' else 'tvshows'
    xbmcplugin.setContent(_HANDLE, kodi_content)
    limit = _page_limit()
    items, next_cursor = fetch_watchlist(mediatype=mediatype, cursor=cursor or None, limit=limit)

    empty_label = '[No Movies in Watchlist]' if mediatype == 'movie' else '[No Shows in Watchlist]'
    if not items:
        _empty(empty_label)
        _end()
        return

    for item in items:
        url, li, is_folder = _build_list_item(item, mediatype_override=mediatype)
        _add_dir(url, li, is_folder)

    if next_cursor:
        next_li = xbmcgui.ListItem(label='Next Page')
        _add_dir(
            _build_url({'action': 'mdblist_watchlist_items', 'mediatype': mediatype, 'cursor': next_cursor}),
            next_li, True,
        )

    _end()


def _view_upnext(offset=0):
    xbmcplugin.setContent(_HANDLE, 'episodes')
    limit  = _page_limit()
    offset = int(offset)
    items, has_more = fetch_upnext(offset=offset, limit=limit)

    if not items:
        _empty('[No Next Episodes Found]')
        _end()
        return

    new_days = _new_episode_days()

    for item in items:
        show     = item.get('show', {})
        next_ep  = item.get('next_episode', {})
        progress = item.get('progress', {})

        tmdb_id = (
            show.get('ids', {}).get('tmdb')
            or item.get('show_tmdbid')
            or item.get('tmdbid')
            or item.get('tmdb_id')
            or item.get('id')
        )
        show_title = (
            show.get('title') or item.get('show_title')
            or item.get('title') or item.get('name') or 'Unknown Show'
        )
        season  = int(next_ep.get('season', 1))
        episode = int(next_ep.get('episode', 1))
        ep_title_fallback = next_ep.get('title') or f'Episode {episode}'

        # TMDB metadata
        show_data, show_overview, show_poster, show_fanart = _get_tmdb_metadata('show', tmdb_id)
        if show_data.get('name'):
            show_title = show_data['name']
        ep_data, ep_plot, ep_thumb, _ = _get_tmdb_metadata('episode', tmdb_id, season, episode)

        # Artwork fallbacks from MDBList
        if not show_poster and show.get('poster'):
            show_poster = f'https://image.tmdb.org/t/p/w500{show["poster"]}'
        if not ep_thumb and next_ep.get('still'):
            ep_thumb = f'https://image.tmdb.org/t/p/w500{next_ep["still"]}'

        ep_title = ep_data.get('name') or ep_title_fallback

        watched = int(progress.get('watched_episode_count', 0))
        total   = int(progress.get('total_episode_count', 0))

        # [NEW] badge
        is_new = False
        air_date_str = next_ep.get('air_date')
        if air_date_str:
            try:
                air_date = datetime.fromisoformat(air_date_str.replace('Z', '+00:00'))
                cutoff   = datetime.now(timezone.utc) - timedelta(days=new_days)
                is_new   = air_date >= cutoff
            except Exception as e:
                xbmc.log(f'[mdblist] Air date parse failed: {e}', xbmc.LOGWARNING)

        new_tag       = '[NEW] ' if is_new else ''
        display_label = (
            f'{new_tag}{show_title} [{watched}/{total}] • '
            f'S{season:02d}E{episode:02d} • {ep_title}'
        )

        li = xbmcgui.ListItem(label=display_label)
        li.setInfo('video', {
            'mediatype': 'episode',
            'tvshowtitle': show_title,
            'title': ep_title,
            'season': season,
            'episode': episode,
            'plot': ep_plot or show_overview or '',
        })
        li.setProperty('IsPlayable', 'true')
        li.setArt({'thumb': ep_thumb, 'poster': show_poster, 'fanart': show_fanart,
                   'icon': ep_thumb or show_poster})

        play_url = _build_url({
            'action': 'play',
            'tmdb_id': tmdb_id,
            'mediatype': 'episode',
            'season': season,
            'episode': episode,
        })
        _add_dir(play_url, li, False)

    if has_more:
        next_li = xbmcgui.ListItem(label='Next Page')
        _add_dir(_build_url({'action': 'mdblist_upnext', 'offset': offset + limit}), next_li, True)

    _end()


# ---------------------------------------------------------------------------
# Public entry point — call this from your addon.py router
# ---------------------------------------------------------------------------

def handle_mdblist_action(params, handle, base_url, addon):
    """
    Dispatch any mdblist_* action from your addon.py router.

    Args:
        params   (dict):           Parsed query params from sys.argv[2].
        handle   (int):            Plugin handle from int(sys.argv[1]).
        base_url (str):            Plugin base URL from sys.argv[0].
        addon    (xbmcaddon.Addon): Your addon instance.

    Usage in addon.py:
        from mdblist import handle_mdblist_action, MDBLIST_ACTIONS

        ADDON    = xbmcaddon.Addon('plugin.video.myaddon')
        HANDLE   = int(sys.argv[1])
        BASE_URL = sys.argv[0]

        # in your router:
        elif action in MDBLIST_ACTIONS:
            handle_mdblist_action(params, HANDLE, BASE_URL, ADDON)
    """
    global _HANDLE, _BASE_URL, _ADDON
    _HANDLE   = handle
    _BASE_URL = base_url
    _ADDON    = addon

    action = params.get('action', '')

    if action == 'mdblist_connect':
        start_device_auth()

    elif action == 'mdblist_menu':
        _view_menu()

    elif action == 'mdblist_my':
        _view_my_lists()

    elif action == 'mdblist_popular':
        _view_popular(params.get('offset', 0))

    elif action == 'mdblist_liked':
        _view_liked()

    elif action == 'mdblist_search':
        _view_search(params.get('query'))

    elif action == 'mdblist_view_list':
        _view_list_contents(params['list_id'], params.get('cursor'))

    elif action == 'mdblist_watchlist_menu':
        _view_watchlist_menu()

    elif action == 'mdblist_watchlist_items':
        _view_watchlist_items(params.get('mediatype', 'movie'), params.get('cursor'))

    elif action == 'mdblist_watchlist_add':
        watchlist_add(
            imdb_id=params.get('imdb_id') or None,
            tmdb_id=params.get('tmdb_id') or None,
            mediatype=params.get('mediatype', 'movie'),
        )

    elif action == 'mdblist_watchlist_remove':
        watchlist_remove(
            imdb_id=params.get('imdb_id') or None,
            tmdb_id=params.get('tmdb_id') or None,
            mediatype=params.get('mediatype', 'movie'),
        )

    elif action == 'mdblist_upnext':
        _view_upnext(params.get('offset', 0))

    else:
        xbmc.log(f'[mdblist] Unknown action: {action}', xbmc.LOGWARNING)
