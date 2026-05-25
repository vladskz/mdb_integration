# MDBList Drop-in Module — Integration Guide

Everything is self-contained in `mdblist.py`: API calls, OAuth, TMDB metadata,
and all Kodi UI rendering. Other addons need **3 changes** only.

---

## Step 1 — Copy the file

```
plugin.video.myaddon/
  addon.py
  addon.xml
  resources/
    lib/
      mdblist.py   ← drop it here
    settings.xml
```

Make sure your `addon.py` puts `resources/lib` on the path:

```python
import sys, os
ADDON_DIR = xbmcaddon.Addon().getAddonInfo('path')
sys.path.append(os.path.join(ADDON_DIR, 'resources', 'lib'))
```

---

## Step 2 — addon.xml dependency

```xml
<import addon="script.module.requests" version="2.22.0"/>
```

---

## Step 3 — Wire up your router (3 lines)

```python
from mdblist import handle_mdblist_action, MDBLIST_ACTIONS

ADDON    = xbmcaddon.Addon('plugin.video.myaddon')
HANDLE   = int(sys.argv[1])
BASE_URL = sys.argv[0]

# inside your router:
elif action in MDBLIST_ACTIONS:
    handle_mdblist_action(params, HANDLE, BASE_URL, ADDON)
```

That's it. Every menu, list browser, watchlist, and Up Next view is
handled internally by `mdblist.py`.

---

## Step 4 — Add settings to settings.xml

### Required

```xml
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
```

### Optional (module reads these if present, uses sensible defaults if absent)

```xml
<setting id="page_limit"       type="number" label="Items Per Page"           default="20" />
<setting id="new_episode_days" type="number" label="NEW badge threshold days"  default="7"  />
<setting id="tmdb_api"         type="text"   label="TMDB API Key"              default=""   />
<setting id="tmdb_language"    type="text"   label="TMDB Language (e.g en-US)" default="en-US" />
```

---

## Adding an MDBList entry point to your main menu

```python
mdb_li = xbmcgui.ListItem(label='📁  MDBList')
xbmcplugin.addDirectoryItem(
    HANDLE,
    build_url({'action': 'mdblist_menu'}),
    mdb_li,
    True
)
```

One item in your menu opens the full MDBList submenu (watchlist, Up Next,
lists, search, account connect).

---

## Full router example

```python
import sys
import urllib.parse
import xbmcaddon, xbmcplugin, xbmcgui, xbmc

from mdblist import handle_mdblist_action, MDBLIST_ACTIONS

ADDON    = xbmcaddon.Addon('plugin.video.myaddon')
HANDLE   = int(sys.argv[1])
BASE_URL = sys.argv[0]

def build_url(query):
    return BASE_URL + '?' + urllib.parse.urlencode(query)

def show_main_menu():
    xbmcplugin.setContent(HANDLE, 'videos')

    # ... your existing menu items ...

    mdb_li = xbmcgui.ListItem(label='📁  MDBList')
    xbmcplugin.addDirectoryItem(HANDLE, build_url({'action': 'mdblist_menu'}), mdb_li, True)
    xbmcplugin.endOfDirectory(HANDLE)

if __name__ == '__main__':
    params = dict(urllib.parse.parse_qsl(sys.argv[2].lstrip('?')))
    action = params.get('action', '')

    if not action:
        show_main_menu()
    elif action == 'play':
        pass  # your play handler
    elif action in MDBLIST_ACTIONS:
        handle_mdblist_action(params, HANDLE, BASE_URL, ADDON)
    else:
        xbmc.log(f'Unknown action: {action}', xbmc.LOGWARNING)
```

---

## Credentials

| Credential | Where | Required for |
|---|---|---|
| API Key | [mdblist.com/preferences](https://mdblist.com/preferences) | All read ops |
| OAuth Client ID | [mdblist.com/developer](https://mdblist.com/developer) — register a *Device Code* app | Watchlist write |

The Bearer and refresh tokens are obtained and stored automatically via
the "Connect MDBList Account" item in the `mdblist_menu` view.

---

## What's handled automatically

- All Kodi `ListItem` creation with TMDB artwork and metadata
- Watchlist "Add / Remove" context menu items on every item
- `[NEW]` badge on Up Next episodes aired within the configured threshold
- Pagination (Next Page) on lists, watchlist, Up Next, and popular lists
- Silent token refresh on expiry; re-auth prompt if refresh fails
- 401 handling clears the stored token and prompts reconnect

---

## Public API surface (if you need raw data)

```python
from mdblist import (
    fetch_user_lists,          # → list[dict]
    fetch_top_lists,           # (offset, limit) → list[dict]
    fetch_liked_lists,         # → list[dict]
    search_lists,              # (query, offset, limit) → list[dict]
    fetch_list_items,          # (list_id, page, limit) → (list[dict], int total)
    fetch_watchlist,           # (mediatype) → list[dict]
    watchlist_add,             # (imdb_id, tmdb_id, mediatype) → bool
    watchlist_remove,          # (imdb_id, tmdb_id, mediatype) → bool
    fetch_upnext,              # (page, limit) → (list[dict], bool has_more)
    start_device_auth,         # () → bool
    refresh_bearer_token,      # () → bool
    is_authenticated,          # () → bool
)
```
