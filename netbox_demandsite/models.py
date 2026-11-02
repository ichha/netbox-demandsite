from django.db import models

class DemandSite(models.Model):
    """
    Model definition for NetBox Demandsite plugin permissions.
    This registers the ContentType and permissions in NetBox so that
    netbox_demandsite appears in NetBox Admin -> Permissions -> Object Types.
    """
    class Meta:
        default_permissions = ('add', 'change', 'delete', 'view')
        managed = False  # Virtual model: no database table created
        verbose_name = 'Demand Site'
        verbose_name_plural = 'Demand Sites'
