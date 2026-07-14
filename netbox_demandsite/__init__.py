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
    default_settings = {
        'api_url': 'https://demandsite.ntc.net.np/api/share/site-dimension',
        'api_token': 'ds_share_7b4a2f8c1e9d3056bf47e382d61a9c8f',
    }

config = NetBoxDemandsiteConfig
