from netbox.plugins import PluginMenu, PluginMenuItem

menu = PluginMenu(
    label='Demandsite',
    icon_class='mdi mdi-database-sync',
    groups=(
        ('Demandsite Data', (
            PluginMenuItem(
                link='plugins:netbox_demandsite:demandsite_list',
                link_text='Sites List'
            ),
        )),
    )
)
