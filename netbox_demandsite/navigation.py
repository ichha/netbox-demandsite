from netbox.plugins import PluginMenu, PluginMenuItem

menu = PluginMenu(
    label='Demandsite',
    icon_class='mdi mdi-database-sync',
    permissions=['netbox_demandsite.view_demandsite'],
    groups=(
        ('Demandsite Data', (
            PluginMenuItem(
                link='plugins:netbox_demandsite:demandsite_list',
                link_text='Sites List',
                permissions=['netbox_demandsite.view_demandsite']
            ),
            PluginMenuItem(
                link='plugins:netbox_demandsite:demandsite_server',
                link_text='Server',
                permissions=['netbox_demandsite.view_demandsite']
            ),
        )),
    )
)
