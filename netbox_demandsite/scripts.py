import logging
from extras.scripts import Script
from .views import auto_sync_missing_demand_sites

logger = logging.getLogger('netbox.plugins.netbox_demandsite')


class DemandsiteDailySyncScript(Script):
    class Meta:
        name = "Demand WSD 24-Hour Auto Sync"
        description = "Checks Demand sites that do not exist in NetBox and synchronizes them"
        scheduling_enabled = True

    def run(self, data, commit):
        self.log_info("Starting Demand WSD 24-Hour Auto Sync (Missing Sites)...")
        result = auto_sync_missing_demand_sites(logger_func=self.log_info)
        self.log_success(
            f"Auto-Sync finished: {result['created']} new sites imported, "
            f"{result['skipped']} existing sites preserved."
        )
        if result['logs']:
            for line in result['logs'][:20]:
                self.log_info(line)
        if result['errors']:
            for err in result['errors'][:10]:
                self.log_failure(err)
        return result
