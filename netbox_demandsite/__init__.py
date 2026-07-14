from netbox.plugins import PluginConfig

class NetBoxDemandsiteConfig(PluginConfig):
    name = 'netbox_demandsite'
    verbose_name = 'Demandsite Site Sync'
    description = 'Read and sync site data from Demandsite server to NetBox'
    version = '0.1.0'
    author = 'Antigravity Developer'
    author_email = 'developer@ntc.net.np'
    base_url = 'demandsite'
    required_settings = []
    default_settings = {}

config = NetBoxDemandsiteConfig
