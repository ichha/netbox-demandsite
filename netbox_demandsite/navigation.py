from netbox.plugins import PluginMenu, PluginMenuItem

menu = PluginMenu(
    label='DemandWSD',
    icon_class='mdi mdi-database-sync',
    groups=(
        ('DemandWSD', (
            PluginMenuItem(
                link='plugins:netbox_demandsite:demandsite_list',
                link_text='DemandSites',
                permissions=['netbox_demandsite.view_demandsite']
            ),
            PluginMenuItem(
                link='plugins:netbox_demandsite:demandsite_server',
                link_text='DemandServer',
                permissions=['netbox_demandsite.view_demandsite']
            ),
            PluginMenuItem(
                link='plugins:netbox_demandsite:demandsite_raw',
                link_text='DemandAPIData',
                permissions=['netbox_demandsite.view_demandsite']
            ),
        )),
    )
)
