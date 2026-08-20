#!/bin/bash
# LiftTracker — convenience script for the weightlifting-equipment repo
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
PIDFILE="/tmp/lifttracker.pid"
PORT="${2:-8080}"

case "$1" in
  install)
    $PYTHON -m pip install -q -r "$SCRIPT_DIR/requirements.txt"
    $PYTHON -m playwright install chromium --with-deps 2>/dev/null || true
    echo "Dependencies installed"
    ;;
  scrape)
    $PYTHON "$SCRIPT_DIR/scraper/run_scrape.py" "${@:2}"
    ;;
  scrape-http)
    $PYTHON "$SCRIPT_DIR/scraper/run_scrape.py" --http-only
    ;;
  dashboard|start)
    if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
      echo "LiftTracker already running (PID $(cat $PIDFILE))"
      exit 1
    fi
    nohup $PYTHON "$SCRIPT_DIR/dashboard.py" --host 0.0.0.0 --port $PORT > /tmp/lifttracker.log 2>&1 &
    echo $! > "$PIDFILE"
    echo "LiftTracker started on http://0.0.0.0:$PORT (PID $!)"
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      kill $(cat "$PIDFILE") 2>/dev/null && echo "LiftTracker stopped" || echo "Process not running"
      rm -f "$PIDFILE"
    else
      echo "Not running"
    fi
    ;;
  status)
    if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
      echo "LiftTracker running (PID $(cat $PIDFILE)) on port $(grep -oP 'port \K\d+' /tmp/lifttracker.log 2>/dev/null || echo $PORT)"
    else
      echo "LiftTracker not running"
    fi
    ;;
  setup-auth)
    $PYTHON "$SCRIPT_DIR/dashboard.py" --setup-auth
    ;;
  migrate)
    exec $PYTHON "$SCRIPT_DIR/migrate_to_neon.py"
    ;;
  db-summary)
    exec $PYTHON "$SCRIPT_DIR/scraper/equipment_db.py" summary
    ;;
  db-history)
    shift
    exec $PYTHON "$SCRIPT_DIR/scraper/equipment_db.py" history "$@"
    ;;
  db-drops)
    shift
    exec $PYTHON "$SCRIPT_DIR/scraper/equipment_db.py" drops "$@"
    ;;
  db-query)
    shift
    exec $PYTHON "$SCRIPT_DIR/scraper/equipment_db.py" query "$@"
    ;;
  *)
    echo "LiftTracker — Weightlifting Equipment Price Tracker"
    echo ""
    echo "Usage: $0 <command> [args]"
    echo ""
    echo "Commands:"
    echo "  install              Install Python + Playwright dependencies"
    echo "  scrape               Run full scrape (all 27+ stores)"
    echo "  scrape-http          HTTP stores only (no browser needed)"
    echo "  start [port]         Start dashboard (default port 8080)"
    echo "  stop                 Stop dashboard"
    echo "  status               Check if dashboard is running"
    echo "  setup-auth           Create or update dashboard users"
    echo "  migrate              Migrate SQLite -> Neon PostgreSQL"
    echo "  db-summary           Database summary by store"
    echo "  db-history <term>    Price history for matching products"
    echo "  db-drops <pct>       Products that dropped >N%"
    echo "  db-query <sql>       Run raw SQL query"
    echo ""
    echo "Examples:"
    echo "  $0 scrape"
    echo "  $0 start 9090"
    echo "  $0 db-history \"Ohio Bar\""
    echo "  $0 db-drops 10"
    ;;
esac