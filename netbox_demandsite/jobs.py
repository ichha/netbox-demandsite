import logging

logger = logging.getLogger('netbox.plugins.netbox_demandsite')

# Attempt to register native NetBox v4 periodic System Job
try:
    from core.choices import JobIntervalChoices
    from netbox.jobs import JobRunner, system_job

    @system_job(interval=JobIntervalChoices.INTERVAL_DAILY)
    class DemandsiteDailyAutoSyncJob(JobRunner):
        class Meta:
            name = "Demandsite 24-Hour Auto Sync"
            description = "Periodically syncs sites and BTS devices from Demand Site API into NetBox every 24 hours."

        def run(self, *args, **kwargs):
            self.logger.info("Executing Demandsite 24-Hour Auto Sync periodic job...")
            from .views import sync_all_demand_sites
            res = sync_all_demand_sites(logger_func=self.logger.info)
            self.logger.info(
                f"Auto sync completed: total={res['total']}, created={res['created']}, "
                f"updated={res['updated']}, unchanged={res['unchanged']}, errors={len(res['errors'])}"
            )
            return res

except Exception as e:
    logger.debug(f"Demandsite system_job could not be registered: {e}")
