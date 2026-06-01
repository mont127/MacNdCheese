#!/usr/bin/env python3
"""
showcase_sync.py — read a Discord forum/"post" channel and publish a showcase.json
that the MacNCheese Store's "Game Showcase" tab renders.

This runs as the GitHub Actions cron worker (see github-workflow.yml). It uses the
Discord REST API directly (no gateway, no discord.py) so it can run as a one-shot
job. Screenshots are *mirrored* into media/ next to showcase.json because Discord
attachment CDN URLs now expire after ~24h — only the mirrored copies stay valid in
the shipped app. Avatar CDN URLs are stable, so those are linked directly.

Inputs (environment variables)
-------------------------------
  DISCORD_BOT_TOKEN     (required)  bot token, used as "Authorization: Bot <token>"
  SHOWCASE_CHANNEL_ID   (required)  the forum/media/text channel to read
  SHOWCASE_GUILD_ID     (optional)  guild id; auto-derived from the channel if absent
  SHOWCASE_REPO         (default "mont127/MacNdCheese")   owner/repo for raw URLs
  SHOWCASE_BRANCH       (default "showcase-data")         branch the data lives on
  OUTPUT_DIR            (default ".")                     where showcase.json + media/ go
  MAX_POSTS             (default 50)
  MAX_SCREENSHOTS       (default 8)    image attachments kept per post (starter msg)
  MAX_COMMENTS          (default 40)   replies kept per post

Output
------
  <OUTPUT_DIR>/showcase.json
  <OUTPUT_DIR>/media/<sha16>.<ext>          mirrored screenshots / comment images
  <OUTPUT_DIR>/media/media_index.json       attachment-id -> filename cache (dedup)

Exit code 0 always on a clean run (even with zero posts). Non-zero only on a hard
configuration/auth error. The script does NOT touch git — the workflow commits.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

API_BASE = "https://discord.com/api/v10"
USER_AGENT = "MacNCheeseShowcaseBot (https://github.com/mont127/MacNdCheese, 1.0)"

# Discord channel types we treat as "post" containers (threads = posts).
FORUM_CHANNEL_TYPES = {15, 16}  # GUILD_FORUM, GUILD_MEDIA
TEXT_CHANNEL_TYPES = {0, 5}     # GUILD_TEXT, GUILD_ANNOUNCEMENT (fallback handling)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif"}


def log(msg: str) -> None:
    print(f"[showcase-sync] {msg}", flush=True)


def die(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"[showcase-sync] FATAL: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Discord REST
# ---------------------------------------------------------------------------

class Discord:
    def __init__(self, token: str) -> None:
        self._auth = f"Bot {token}"

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        for attempt in range(6):
            req = urllib.request.Request(url, method="GET")
            req.add_header("Authorization", self._auth)
            req.add_header("User-Agent", USER_AGENT)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429:  # rate limited
                    retry_after = 1.0
                    try:
                        body = json.loads(exc.read().decode("utf-8"))
                        retry_after = float(body.get("retry_after", 1.0))
                    except Exception:
                        pass
                    log(f"rate limited on {path}; sleeping {retry_after:.2f}s")
                    time.sleep(min(retry_after + 0.25, 10.0))
                    continue
                if exc.code in (500, 502, 503, 504):
                    time.sleep(1.0 + attempt)
                    continue
                detail = ""
                try:
                    detail = exc.read().decode("utf-8")[:300]
                except Exception:
                    pass
                raise RuntimeError(f"GET {path} -> HTTP {exc.code} {detail}") from exc
        raise RuntimeError(f"GET {path} -> exhausted retries")

    def get_optional(self, path: str) -> Optional[Any]:
        """Like get(), but returns None on 404/403 instead of raising."""
        try:
            return self.get(path)
        except RuntimeError as exc:
            if "HTTP 404" in str(exc) or "HTTP 403" in str(exc):
                return None
            raise


# ---------------------------------------------------------------------------
# Image mirroring
# ---------------------------------------------------------------------------

class MediaMirror:
    def __init__(self, media_dir: str, raw_base: str) -> None:
        self.media_dir = media_dir
        self.raw_base = raw_base.rstrip("/")
        self.index_path = os.path.join(media_dir, "media_index.json")
        os.makedirs(media_dir, exist_ok=True)
        self.index: Dict[str, str] = {}
        if os.path.exists(self.index_path):
            try:
                with open(self.index_path, "r", encoding="utf-8") as fh:
                    self.index = json.load(fh)
            except Exception:
                self.index = {}
        self.referenced: set[str] = set()  # filenames used by the current feed

    @staticmethod
    def _ext_for(attachment: Dict[str, Any]) -> str:
        name = (attachment.get("filename") or "").lower()
        _, ext = os.path.splitext(name)
        if ext in IMAGE_EXTS:
            return ".png" if ext == ".jpeg" else ext
        ctype = (attachment.get("content_type") or "").lower()
        mapping = {
            "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
            "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp",
        }
        return mapping.get(ctype.split(";")[0].strip(), ".png")

    @staticmethod
    def is_image(attachment: Dict[str, Any]) -> bool:
        ctype = (attachment.get("content_type") or "").lower()
        if ctype.startswith("image/"):
            return True
        name = (attachment.get("filename") or "").lower()
        _, ext = os.path.splitext(name)
        return ext in IMAGE_EXTS

    def mirror(self, attachment: Dict[str, Any]) -> Optional[str]:
        """Mirror one image attachment, returning its public raw URL (or None)."""
        if not self.is_image(attachment):
            return None
        att_id = str(attachment.get("id") or "")
        url = attachment.get("url")
        if not url:
            return None

        # Reuse a previously-downloaded file for this attachment id.
        cached = self.index.get(att_id)
        if cached and os.path.exists(os.path.join(self.media_dir, cached)):
            self.referenced.add(cached)
            return f"{self.raw_base}/media/{cached}"

        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", USER_AGENT)
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
        except Exception as exc:  # don't fail the whole run for one bad image
            log(f"warning: failed to download attachment {att_id}: {exc}")
            return None

        sha = hashlib.sha256(data).hexdigest()[:16]
        filename = sha + self._ext_for(attachment)
        dest = os.path.join(self.media_dir, filename)
        if not os.path.exists(dest):
            with open(dest, "wb") as fh:
                fh.write(data)
        self.index[att_id] = filename
        self.referenced.add(filename)
        return f"{self.raw_base}/media/{filename}"

    def finalize(self) -> None:
        """Prune unreferenced media + index entries; persist the index."""
        # Drop index entries pointing at files no longer referenced.
        self.index = {aid: fn for aid, fn in self.index.items() if fn in self.referenced}
        keep = set(self.referenced) | {"media_index.json"}
        for fn in os.listdir(self.media_dir):
            if fn not in keep:
                try:
                    os.remove(os.path.join(self.media_dir, fn))
                except OSError:
                    pass
        with open(self.index_path, "w", encoding="utf-8") as fh:
            json.dump(self.index, fh, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Discord helpers
# ---------------------------------------------------------------------------

def avatar_url(user: Dict[str, Any]) -> Optional[str]:
    if not user:
        return None
    uid = str(user.get("id") or "")
    avatar = user.get("avatar")
    if uid and avatar:
        ext = "gif" if str(avatar).startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{uid}/{avatar}.{ext}?size=64"
    # Default avatar. New username system: (id >> 22) % 6; legacy: discriminator % 5.
    try:
        disc = int(user.get("discriminator") or "0")
    except ValueError:
        disc = 0
    if disc and disc != 0:
        idx = disc % 5
    elif uid:
        idx = (int(uid) >> 22) % 6
    else:
        idx = 0
    return f"https://cdn.discordapp.com/embed/avatars/{idx}.png"


def display_name(user: Dict[str, Any]) -> str:
    if not user:
        return "Unknown"
    return user.get("global_name") or user.get("username") or "Unknown"


def collect_images(message: Dict[str, Any], mirror: MediaMirror, limit: int) -> List[str]:
    out: List[str] = []
    for att in message.get("attachments") or []:
        if len(out) >= limit:
            break
        url = mirror.mirror(att)
        if url:
            out.append(url)
    return out


def paginate_messages(dc: Discord, thread_id: str, want: int) -> List[Dict[str, Any]]:
    """Fetch up to `want` newest messages from a thread/channel (newest-first)."""
    out: List[Dict[str, Any]] = []
    before: Optional[str] = None
    while len(out) < want:
        params: Dict[str, Any] = {"limit": min(100, want - len(out) + 1)}
        if before:
            params["before"] = before
        batch = dc.get(f"/channels/{thread_id}/messages", params)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < params["limit"]:
            break
        before = batch[-1]["id"]
    return out


def list_forum_threads(dc: Discord, guild_id: str, channel_id: str, want: int) -> List[Dict[str, Any]]:
    """Active + recently-archived public threads (posts) under a forum/media channel."""
    threads: List[Dict[str, Any]] = []
    seen: set[str] = set()

    active = dc.get_optional(f"/guilds/{guild_id}/threads/active") or {}
    for th in active.get("threads", []):
        if str(th.get("parent_id")) == str(channel_id) and th["id"] not in seen:
            seen.add(th["id"])
            threads.append(th)

    before: Optional[str] = None
    while len(threads) < want:
        params: Dict[str, Any] = {"limit": 100}
        if before:
            params["before"] = before
        page = dc.get(f"/channels/{channel_id}/threads/archived/public", params)
        page_threads = page.get("threads", []) if isinstance(page, dict) else []
        for th in page_threads:
            if th["id"] not in seen:
                seen.add(th["id"])
                threads.append(th)
        if not page_threads or not (isinstance(page, dict) and page.get("has_more")):
            break
        # archived threads paginate by archive_timestamp of the last item
        last = page_threads[-1]
        before = (last.get("thread_metadata") or {}).get("archive_timestamp")
        if not before:
            break

    # Newest first by create timestamp.
    def created(th: Dict[str, Any]) -> str:
        return (th.get("thread_metadata") or {}).get("create_timestamp") or th.get("id") or ""

    threads.sort(key=created, reverse=True)
    return threads[:want]


def build_post_from_thread(
    dc: Discord, guild_id: str, channel_id: str, thread: Dict[str, Any],
    tag_names: Dict[str, str], mirror: MediaMirror, max_shots: int, max_comments: int,
) -> Optional[Dict[str, Any]]:
    thread_id = str(thread["id"])

    # The forum starter message shares the thread's id. Fall back to the oldest msg.
    starter = dc.get_optional(f"/channels/{thread_id}/messages/{thread_id}")
    recent = paginate_messages(dc, thread_id, max_comments + 1)
    if starter is None and recent:
        starter = min(recent, key=lambda m: m["id"])  # oldest id == oldest message
    if starter is None:
        # Empty post (no readable messages) — still show the title.
        starter = {}

    author = starter.get("author") or {}
    created_at = (thread.get("thread_metadata") or {}).get("create_timestamp") \
        or starter.get("timestamp") or ""

    tags = [tag_names[t] for t in (thread.get("applied_tags") or []) if t in tag_names]

    comments: List[Dict[str, Any]] = []
    for msg in recent:
        if str(msg.get("id")) == thread_id:
            continue  # that's the starter
        if (msg.get("type") or 0) not in (0, 19, 21):  # default / reply / thread-starter
            continue
        text = (msg.get("content") or "").strip()
        imgs = collect_images(msg, mirror, max_shots)
        if not text and not imgs:
            continue
        muser = msg.get("author") or {}
        comments.append({
            "author": display_name(muser),
            "avatar_url": avatar_url(muser),
            "created_at": msg.get("timestamp") or "",
            "text": text,
            "images": imgs,
        })
    comments.reverse()  # chronological (oldest first)
    comments = comments[:max_comments]

    return {
        "id": thread_id,
        "title": thread.get("name") or "Untitled",
        "author": {"name": display_name(author), "avatar_url": avatar_url(author)},
        "created_at": created_at,
        "tags": tags,
        "body": (starter.get("content") or "").strip(),
        "screenshots": collect_images(starter, mirror, max_shots),
        "comments": comments,
        "url": f"https://discord.com/channels/{guild_id}/{thread_id}",
    }


def build_posts_from_text_channel(
    dc: Discord, guild_id: str, channel_id: str, mirror: MediaMirror,
    max_posts: int, max_shots: int,
) -> List[Dict[str, Any]]:
    """Fallback: a plain text channel — each message with an image becomes a post."""
    msgs = paginate_messages(dc, channel_id, max_posts * 2)
    posts: List[Dict[str, Any]] = []
    for msg in msgs:
        shots = collect_images(msg, mirror, max_shots)
        text = (msg.get("content") or "").strip()
        if not shots:  # showcase needs at least a screenshot
            continue
        user = msg.get("author") or {}
        title = (text.splitlines()[0] if text else f"Post by {display_name(user)}")[:120]
        posts.append({
            "id": str(msg["id"]),
            "title": title or "Untitled",
            "author": {"name": display_name(user), "avatar_url": avatar_url(user)},
            "created_at": msg.get("timestamp") or "",
            "tags": [],
            "body": text,
            "screenshots": shots,
            "comments": [],
            "url": f"https://discord.com/channels/{guild_id}/{channel_id}/{msg['id']}",
        })
        if len(posts) >= max_posts:
            break
    return posts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _posts_signature(feed: Dict[str, Any]) -> str:
    """Stable hash of everything except generated_at, to detect real changes."""
    clone = {k: v for k, v in feed.items() if k != "generated_at"}
    return hashlib.sha256(json.dumps(clone, sort_keys=True).encode("utf-8")).hexdigest()


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("SHOWCASE_CHANNEL_ID", "").strip()
    guild_id = os.environ.get("SHOWCASE_GUILD_ID", "").strip()
    repo = os.environ.get("SHOWCASE_REPO", "mont127/MacNdCheese").strip()
    branch = os.environ.get("SHOWCASE_BRANCH", "showcase-data").strip()
    out_dir = os.environ.get("OUTPUT_DIR", ".").strip() or "."
    max_posts = int(os.environ.get("MAX_POSTS", "50"))
    max_shots = int(os.environ.get("MAX_SCREENSHOTS", "8"))
    max_comments = int(os.environ.get("MAX_COMMENTS", "40"))

    if not token:
        die("DISCORD_BOT_TOKEN is not set")
    if not channel_id:
        log("SHOWCASE_CHANNEL_ID is not set — writing an empty showcase.json")

    raw_base = f"https://raw.githubusercontent.com/{repo}/{branch}"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "showcase.json")
    mirror = MediaMirror(os.path.join(out_dir, "media"), raw_base)

    posts: List[Dict[str, Any]] = []
    channel_name = ""

    if channel_id:
        dc = Discord(token)
        channel = dc.get_optional(f"/channels/{channel_id}")
        if channel is None:
            die(f"channel {channel_id} not found or bot lacks access "
                "(needs View Channel + Read Message History)")
        channel_name = channel.get("name") or ""
        ctype = int(channel.get("type", -1))
        if not guild_id:
            guild_id = str(channel.get("guild_id") or "")
        log(f"channel #{channel_name} id={channel_id} type={ctype} guild={guild_id}")

        if ctype in FORUM_CHANNEL_TYPES:
            tag_names = {str(t["id"]): t.get("name", "") for t in (channel.get("available_tags") or [])}
            threads = list_forum_threads(dc, guild_id, channel_id, max_posts)
            log(f"found {len(threads)} post(s)")
            for th in threads:
                try:
                    post = build_post_from_thread(
                        dc, guild_id, channel_id, th, tag_names, mirror, max_shots, max_comments)
                    if post:
                        posts.append(post)
                except Exception as exc:
                    log(f"warning: skipping thread {th.get('id')}: {exc}")
        elif ctype in TEXT_CHANNEL_TYPES:
            log("text channel — using message-as-post fallback")
            posts = build_posts_from_text_channel(
                dc, guild_id, channel_id, mirror, max_posts, max_shots)
        else:
            die(f"channel type {ctype} is not a forum/media/text channel")

    feed = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "channel_id": channel_id,
        "channel_name": channel_name,
        "posts": posts,
    }

    # Only rewrite (and thus create a commit) when the content actually changed.
    new_sig = _posts_signature(feed)
    old_sig = None
    if os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                old_sig = _posts_signature(json.load(fh))
        except Exception:
            old_sig = None

    mirror.finalize()

    if new_sig == old_sig:
        log(f"no change ({len(posts)} post(s)); leaving showcase.json untouched")
        return

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(feed, fh, indent=2, ensure_ascii=False)
    log(f"wrote {out_path} with {len(posts)} post(s)")


if __name__ == "__main__":
    main()
