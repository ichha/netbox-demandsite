from netbox.plugins import PluginMenu, PluginMenuItem

menu = PluginMenu(
    label='Demand WSD',
    icon_class='mdi mdi-database-sync',
    groups=(
        ('Demand WSD', (
            PluginMenuItem(
                link='plugins:netbox_demandsite:demandsite_list',
                link_text='Demand Sites',
                permissions=['netbox_demandsite.view_demandsite']
            ),
            PluginMenuItem(
                link='plugins:netbox_demandsite:demandsite_server',
                link_text='Demand Server',
                permissions=['netbox_demandsite.view_demandsite']
            ),
            PluginMenuItem(
                link='plugins:netbox_demandsite:demandsite_raw',
                link_text='Demand API Data',
                permissions=['netbox_demandsite.view_demandsite']
            ),
            PluginMenuItem(
                link='plugins:netbox_demandsite:demandsite_logs',
                link_text='Sync Logs',
                permissions=['netbox_demandsite.view_demandsite']
            ),
        )),
    )
)
