import logging

logger = logging.getLogger('netbox.plugins.netbox_demandsite')

# Attempt to register native NetBox v4 periodic System Job
try:
    from core.choices import JobIntervalChoices
    from netbox.jobs import JobRunner, system_job

    @system_job(interval=JobIntervalChoices.INTERVAL_DAILY)
    class DemandsiteDailyAutoSyncJob(JobRunner):
        class Meta:
            name = "Demand WSD 24-Hour Auto Sync"
            description = "Checks every 24 hours for sites in Demand that do not exist in NetBox, and imports them."

        def run(self, *args, **kwargs):
            self.logger.info("Executing Demand WSD 24-Hour Auto Sync (Checking missing sites)...")
            try:
                from .views import auto_sync_missing_demand_sites
                res = auto_sync_missing_demand_sites(source='24-Hour Auto-Sync (Worker)', logger_func=self.logger.info)
                self.logger.info(
                    f"Auto sync completed: {res.get('created', 0)} new sites imported, "
                    f"{res.get('skipped', 0)} existing sites preserved, {len(res.get('errors', []))} errors."
                )
                return res
            except Exception as e:
                self.logger.error(f"Error during Demandsite Daily Auto Sync: {e}")
                raise e

except Exception as e:
    logger.debug(f"Demandsite system_job could not be registered: {e}")
