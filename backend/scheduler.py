import threading
import time
from price_service import update_all_prices
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_scheduler(interval_hours=24):
    """Run the price update scheduler in a separate thread."""
    from status import record_price_update

    def scheduler_thread():
        while True:
            try:
                logger.info("Starting scheduled price update...")
                update_all_prices()
                logger.info("Scheduled price update completed.")
                record_price_update(True)
            except Exception as e:
                logger.error(f"Error in price update scheduler: {e}")
                record_price_update(False, str(e))
            time.sleep(interval_hours * 60 * 60)
    
    thread = threading.Thread(target=scheduler_thread, daemon=True)
    thread.start()
    logger.info(f"Price update scheduler started (interval: {interval_hours} hours)")
    return thread

def start_scheduler():
    """Start the scheduler with default 24-hour interval."""
    # Allow override via environment variable for production deployments
    import os
    hours = os.getenv("PRICE_UPDATE_INTERVAL_HOURS")
    if hours:
        try:
            interval = int(hours)
        except ValueError:
            interval = 24
    else:
        interval = 24
    return run_scheduler(interval_hours=interval)
