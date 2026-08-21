#!/usr/bin/env python3
"""
Scrape orchestrator: runs all equipment sites, saves JSON, and ingests the
results into whichever database is configured (Neon when DATABASE_URL is set,
SQLite otherwise).

Usage:
    python scraper/run_scrape.py              # Full scrape (HTTP + browser)
    python scraper/run_scrape.py --http-only   # HTTP stores only
    python scraper/run_scrape.py --site rogue  # Single site
"""
import sys, os, json, time, argparse, threading
from pathlib import Path
from datetime import datetime, timezone

# Add scraper directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from equipment_scraper import scrape_all, to_json
from db import connect, init_schema
from ingest import ingest

OUTPUT_DIR = Path(__file__).resolve().parent.parent / 'data'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HEARTBEAT_SECONDS = 30
KEEP_SCRAPE_FILES = int(os.environ.get('KEEP_SCRAPE_FILES', '14'))


def _now_str():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _update_run(run_id, **fields):
    """Update a scrape_runs row (dashboard.py inserts it before launching this
    process) so scrape status/history is visible in the UI."""
    if run_id is None or not fields:
        return
    assignments = ', '.join(f'{k} = ?' for k in fields)
    params = list(fields.values()) + [run_id]
    try:
        with connect() as db:
            db.execute(f'UPDATE scrape_runs SET {assignments} WHERE id = ?', params)
    except Exception as e:
        print(f'scrape_runs update error: {e}', flush=True)


def _start_heartbeat(run_id):
    """Touch heartbeat_at periodically so the dashboard can tell a live scrape
    from one whose process was killed (Replit recycles containers mid-run)."""
    if run_id is None:
        return lambda: None
    stop = threading.Event()

    def beat():
        while not stop.wait(HEARTBEAT_SECONDS):
            _update_run(run_id, heartbeat_at=_now_str())

    t = threading.Thread(target=beat, daemon=True)
    t.start()
    return stop.set


def _prune_old_scrapes():
    """Keep the last N dated scrape files; they are ~3 MB each."""
    files = sorted(OUTPUT_DIR.glob('scrape_20*.json'))
    for old in files[:-KEEP_SCRAPE_FILES] if len(files) > KEEP_SCRAPE_FILES else []:
        try:
            old.unlink()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description='Weightlifting equipment price scraper')
    parser.add_argument('--http-only', action='store_true', help='Skip Playwright sites')
    parser.add_argument('--site', '-s', help='Scrape a single site')
    parser.add_argument('--output', '-o', help='Output JSON path')
    parser.add_argument('--run-id', type=int, help='scrape_runs row to update with progress/result')
    # Shopify's rate limit is per IP across all storefronts, so more workers
    # means more 429s, not more throughput. 3 plus http_get's per-host spacing
    # completes a full run without tripping it.
    parser.add_argument('--workers', type=int,
                        default=int(os.environ.get('SCRAPE_WORKERS', '3')),
                        help='Parallel site workers (default 3)')
    args = parser.parse_args()

    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M')
    print(f'Scrape starting at {date_str}', flush=True)
    stop_heartbeat = _start_heartbeat(args.run_id)
    _update_run(args.run_id, heartbeat_at=_now_str())

    try:
        has_browser = False
        if not args.http_only:
            try:
                import playwright  # noqa: F401
                has_browser = True
            except ImportError:
                print('Playwright not available, skipping browser sites', flush=True)

        results, products = scrape_all(site_filter=args.site, use_browser=has_browser,
                                       workers=args.workers)

        # Surface partial failures rather than silently shipping a thin scrape.
        failed = [r for r in results if r.get('status') == 'error']
        blocked = [r for r in results if r.get('status') == 'blocked']
        empty = [r for r in results
                 if r.get('status') in ('ok', 'empty') and not r.get('products')]
        for r in failed:
            print(f"  !! {r['site']} failed: {r.get('error')}", flush=True)
        for r in blocked:
            print(f"  !! {r['site']} was rate-limited/blocked: {r.get('error')}", flush=True)
        for r in empty:
            print(f"  !! {r['site']} returned 0 products", flush=True)

        if not products:
            _update_run(args.run_id, status='error', error='No products scraped',
                        finished_at=_now_str())
            return 1

        output_path = Path(args.output or (OUTPUT_DIR / f'scrape_{date_str}.json'))
        payload = to_json(products)
        output_path.write_text(payload)
        # Only a full run may claim to be "latest" — a --site run holds one
        # store's products and would otherwise masquerade as a complete scrape.
        if not args.site:
            (OUTPUT_DIR / 'scrape_latest.json').write_text(payload)
        print(f'{len(products)} products -> {output_path}', flush=True)
        _prune_old_scrapes()

        # Single ingest path — same code for SQLite and Neon.
        with connect() as db:
            init_schema(db)
            stats = ingest(products, db=db)

        try:
            from product_matching import compute_matches
            print('Running product matching...', flush=True)
            compute_matches()
        except ImportError as e:
            print(f'Product matching skipped (rapidfuzz not installed?): {e}', flush=True)
        except Exception as e:
            print(f'Product matching error: {e}', flush=True)

        note = None
        broken = [r['site'] for r in failed + blocked + empty]
        if broken:
            note = f"{len(broken)} store(s) returned nothing: {', '.join(broken[:8])}"
            if len(broken) > 8:
                note += f' (+{len(broken) - 8} more)'
        print('Done', flush=True)
        _update_run(args.run_id, status='success', products_scraped=stats['products'],
                    error=note, finished_at=_now_str())
        return 0
    except Exception as e:
        print(f'Scrape failed: {e}', flush=True)
        _update_run(args.run_id, status='error', error=str(e)[:500],
                    finished_at=_now_str())
        raise
    finally:
        stop_heartbeat()


if __name__ == '__main__':
    sys.exit(main() or 0)
