import logging
import requests
from decimal import Decimal
from django.shortcuts import render, redirect
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from dcim.models import Site, Device
from extras.models import CustomField
from django.contrib.contenttypes.models import ContentType

logger = logging.getLogger('netbox.plugins.netbox_demandsite')

def get_cf_key(site, keywords):
    """
    Finds a key in the site's custom_field_data dictionary
    that contains all the specified keywords (case-insensitive).
    """
    if not site or not site.custom_field_data:
        return None
    for key in site.custom_field_data.keys():
        key_lower = key.lower()
        if all(kw in key_lower for kw in keywords):
            return key
    return None

def get_site_id_cf_name():
    """
    Dynamically finds the custom field name that stores the Site ID.
    Looks for a custom field containing 'site' and 'id' (case-insensitive).
    """
    site_ct = ContentType.objects.get_for_model(Site)
    try:
        cf_fields = CustomField.objects.filter(object_types=site_ct)
        for cf in cf_fields:
            if 'site' in cf.name.lower() and 'id' in cf.name.lower():
                return cf.name
    except Exception:
        pass
    try:
        cf_fields = CustomField.objects.filter(content_types=site_ct)
        for cf in cf_fields:
            if 'site' in cf.name.lower() and 'id' in cf.name.lower():
                return cf.name
    except Exception:
        pass
    for site in Site.objects.all()[:20]:
        if site.custom_field_data:
            for key in site.custom_field_data.keys():
                if 'site' in key.lower() and 'id' in key.lower():
                    return key
    return 'site_id'

def sync_one_site(netbox_site, api_site, cf_name):
    """
    Synchronizes standard fields and all custom fields (District, Palika, Ward)
    from external API site data to a NetBox site instance.
    """
    updated = False
    
    # 1. Sync Coordinates (Latitude / Longitude)
    api_lat = api_site.get('latitude')
    api_lon = api_site.get('longitude')
    if api_lat:
        try:
            dec_lat = Decimal(str(api_lat))
            if not netbox_site.latitude or abs(netbox_site.latitude - dec_lat) > Decimal('0.00001'):
                netbox_site.latitude = dec_lat
                updated = True
        except Exception:
            pass
    if api_lon:
        try:
            dec_lon = Decimal(str(api_lon))
            if not netbox_site.longitude or abs(netbox_site.longitude - dec_lon) > Decimal('0.00001'):
                netbox_site.longitude = dec_lon
                updated = True
        except Exception:
            pass
            
    # 2. Sync Status
    api_status = api_site.get('status')
    if api_status == 'Operational' and netbox_site.status != 'active':
        netbox_site.status = 'active'
        updated = True
    elif api_status == 'Planned' and netbox_site.status != 'planned':
        netbox_site.status = 'planned'
        updated = True
        
    # 3. Sync Description containing Local Divisions
    desc_parts = []
    if api_site.get('province'):
        desc_parts.append(f"Province: {api_site.get('province')}")
    if api_site.get('district'):
        desc_parts.append(f"District: {api_site.get('district')}")
    if api_site.get('palika'):
        desc_parts.append(f"Palika: {api_site.get('palika')}")
    new_desc = " | ".join(desc_parts)
    if new_desc and netbox_site.description != new_desc:
        netbox_site.description = new_desc
        updated = True

    # 4. Sync Custom Fields (District, Local Level Name, Local Level, Ward)
    district_key = get_cf_key(netbox_site, ['district'])
    local_level_name_key = get_cf_key(netbox_site, ['local', 'level', 'name']) or get_cf_key(netbox_site, ['palika'])
    local_level_type_key = get_cf_key(netbox_site, ['local', 'level']) or get_cf_key(netbox_site, ['palika', 'type'])
    if local_level_type_key == local_level_name_key:
        local_level_type_key = None
    ward_key = get_cf_key(netbox_site, ['ward'])
    
    if district_key and api_site.get('district'):
        val = api_site.get('district')
        if netbox_site.custom_field_data.get(district_key) != val:
            netbox_site.custom_field_data[district_key] = val
            updated = True
            
    if local_level_name_key and api_site.get('palika'):
        val = api_site.get('palika')
        if netbox_site.custom_field_data.get(local_level_name_key) != val:
            netbox_site.custom_field_data[local_level_name_key] = val
            updated = True
            
    if local_level_type_key and api_site.get('palika_type'):
        val = api_site.get('palika_type')
        if val == 'RuralMunicipality':
            val = 'Rural Municipality'
        if netbox_site.custom_field_data.get(local_level_type_key) != val:
            netbox_site.custom_field_data[local_level_type_key] = val
            updated = True
            
    if ward_key and api_site.get('wardno') is not None:
        val = api_site.get('wardno')
        try:
            existing_type = type(netbox_site.custom_field_data.get(ward_key))
            if existing_type is int:
                val = int(val)
            else:
                val = str(val)
            if netbox_site.custom_field_data.get(ward_key) != val:
                netbox_site.custom_field_data[ward_key] = val
                updated = True
        except Exception:
            pass
            
    if updated:
        netbox_site.save()
        return True
    return False


class DemandsiteListView(LoginRequiredMixin, View):
    template_name = 'netbox_demandsite/demandsite_list.html'

    def _get_api_data(self):
        url = "https://demandsite.ntc.net.np/api/share/site-dimension"
        from django.conf import settings
        plugin_config = settings.PLUGINS_CONFIG.get('netbox_demandsite', {})
        url = plugin_config.get('api_url', url)
        api_token = plugin_config.get('api_token', 'ds_share_7b4a2f8c1e9d3056bf47e382d61a9c8f')
        
        headers = {
            "Authorization": f"Bearer {api_token}"
        }
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            return response.json(), None
        except Exception as e:
            logger.error(f"Error fetching Demandsite data: {e}")
            return [], str(e)

    def get(self, request):
        api_sites, api_error = self._get_api_data()
        if api_error:
            messages.error(request, f"Failed to fetch site data from external server: {api_error}")
            
        cf_name = get_site_id_cf_name()
        
        # Build mapping of NetBox sites by Site ID custom field (case-insensitive)
        netbox_sites_map = {}
        for site in Site.objects.all():
            if site.custom_field_data:
                site_id_val = site.custom_field_data.get(cf_name)
                if site_id_val:
                    netbox_sites_map[str(site_id_val).strip().upper()] = site
                    
        # Check if we should display the side-by-side comparison for a specific Site ID
        selected_site_id = request.GET.get('site_id')
        
        # Also check if query is an exact site id match
        search_q = request.GET.get('q', '').strip().upper()
        if not selected_site_id and search_q:
            # Check if search_q matches exactly any siteid in the API
            exact_match = next((x for x in api_sites if str(x.get('siteid')).strip().upper() == search_q), None)
            if exact_match:
                selected_site_id = exact_match.get('siteid')

        if selected_site_id:
            api_site = next((x for x in api_sites if str(x.get('siteid')).strip().upper() == str(selected_site_id).strip().upper()), None)
            if not api_site:
                messages.error(request, f"Site ID {selected_site_id} not found in API.")
                return redirect('plugins:netbox_demandsite:demandsite_list')
                
            netbox_site = netbox_sites_map.get(str(selected_site_id).strip().upper())
            
            nb_data = {}
            nb_devices = []
            
            if netbox_site:
                district_key = get_cf_key(netbox_site, ['district'])
                local_level_name_key = get_cf_key(netbox_site, ['local', 'level', 'name']) or get_cf_key(netbox_site, ['palika'])
                local_level_type_key = get_cf_key(netbox_site, ['local', 'level']) or get_cf_key(netbox_site, ['palika', 'type'])
                if local_level_type_key == local_level_name_key:
                    local_level_type_key = None
                ward_key = get_cf_key(netbox_site, ['ward'])
                
                nb_data = {
                    'site_id': netbox_site.custom_field_data.get(cf_name, '—'),
                    'name': netbox_site.name,
                    'region': netbox_site.region.name if netbox_site.region else '—',
                    'district': netbox_site.custom_field_data.get(district_key, '—') if district_key else '—',
                    'local_level_name': netbox_site.custom_field_data.get(local_level_name_key, '—') if local_level_name_key else '—',
                    'local_level': netbox_site.custom_field_data.get(local_level_type_key, '—') if local_level_type_key else '—',
                    'ward': netbox_site.custom_field_data.get(ward_key, '—') if ward_key else '—',
                    'latitude': netbox_site.latitude,
                    'longitude': netbox_site.longitude,
                    'status': netbox_site.get_status_display() if hasattr(netbox_site, 'get_status_display') else str(netbox_site.status),
                }
                
                # Fetch devices under Site
                devices = Device.objects.filter(site=netbox_site)
                for d in devices:
                    role_name = d.device_role.name if d.device_role else "—"
                    type_name = d.device_type.model if d.device_type else "—"
                    nb_devices.append({
                        'name': d.name,
                        'status': d.get_status_display() if hasattr(d, 'get_status_display') else str(d.status),
                        'role': role_name,
                        'type': type_name,
                        'absolute_url': d.get_absolute_url() if hasattr(d, 'get_absolute_url') else "#"
                    })
            
            context = {
                'show_comparison': True,
                'siteid': selected_site_id,
                'api_site': api_site,
                'netbox_site': netbox_site,
                'nb_data': nb_data,
                'nb_devices': nb_devices,
                'cf_name': cf_name,
                'q': request.GET.get('q', '')
            }
            return render(request, self.template_name, context)

        # Standard List View
        correlated_sites = []
        total_matched = 0
        total_planned = 0
        total_operational = 0
        
        for item in api_sites:
            siteid = item.get('siteid', '')
            status = item.get('status', '')
            if status == 'Planned':
                total_planned += 1
            elif status == 'Operational':
                total_operational += 1
                
            matched_site = netbox_sites_map.get(str(siteid).strip().upper())
            
            sync_status = "Missing in NetBox"
            sync_color = "danger"
            needs_sync = False
            
            if matched_site:
                total_matched += 1
                lat_diff = False
                lon_diff = False
                status_diff = False
                cf_diff = False
                
                api_lat = item.get('latitude')
                api_lon = item.get('longitude')
                
                # Check status
                if status == 'Operational' and matched_site.status != 'active':
                    status_diff = True
                elif status == 'Planned' and matched_site.status != 'planned':
                    status_diff = True
                    
                # Compare coordinates
                if api_lat:
                    try:
                        if not matched_site.latitude or abs(float(matched_site.latitude) - float(api_lat)) > 0.00001:
                            lat_diff = True
                    except (ValueError, TypeError):
                        pass
                if api_lon:
                    try:
                        if not matched_site.longitude or abs(float(matched_site.longitude) - float(api_lon)) > 0.00001:
                            lon_diff = True
                    except (ValueError, TypeError):
                        pass

                # Compare custom fields
                district_key = get_cf_key(matched_site, ['district'])
                local_level_name_key = get_cf_key(matched_site, ['local', 'level', 'name']) or get_cf_key(matched_site, ['palika'])
                local_level_type_key = get_cf_key(matched_site, ['local', 'level']) or get_cf_key(matched_site, ['palika', 'type'])
                if local_level_type_key == local_level_name_key:
                    local_level_type_key = None
                ward_key = get_cf_key(matched_site, ['ward'])

                if district_key and item.get('district') and matched_site.custom_field_data.get(district_key) != item.get('district'):
                    cf_diff = True
                if local_level_name_key and item.get('palika') and matched_site.custom_field_data.get(local_level_name_key) != item.get('palika'):
                    cf_diff = True
                if local_level_type_key and item.get('palika_type'):
                    val = item.get('palika_type')
                    if val == 'RuralMunicipality':
                        val = 'Rural Municipality'
                    if matched_site.custom_field_data.get(local_level_type_key) != val:
                        cf_diff = True
                if ward_key and item.get('wardno') is not None:
                    # check string format
                    if str(matched_site.custom_field_data.get(ward_key)) != str(item.get('wardno')):
                        cf_diff = True
                    
                if lat_diff or lon_diff or status_diff or cf_diff:
                    sync_status = "Out of Sync"
                    sync_color = "warning"
                    needs_sync = True
                else:
                    sync_status = "Synchronized"
                    sync_color = "success"
                    
            if search_q:
                site_name1 = item.get('sitename1', '').upper()
                site_name2 = item.get('sitename2', '').upper()
                province = item.get('province', '').upper()
                district = item.get('district', '').upper()
                palika = item.get('palika', '').upper()
                if (search_q not in str(siteid).upper() and 
                    search_q not in site_name1 and 
                    search_q not in site_name2 and 
                    search_q not in province and 
                    search_q not in district and 
                    search_q not in palika):
                    continue
                    
            correlated_sites.append({
                'api_data': item,
                'netbox_site': matched_site,
                'sync_status': sync_status,
                'sync_color': sync_color,
                'needs_sync': needs_sync
            })
            
        context = {
            'show_comparison': False,
            'correlated_sites': correlated_sites,
            'total_sites': len(api_sites),
            'total_matched': total_matched,
            'total_missing': len(api_sites) - total_matched,
            'total_planned': total_planned,
            'total_operational': total_operational,
            'cf_name': cf_name,
            'q': request.GET.get('q', '')
        }
        return render(request, self.template_name, context)

    def post(self, request):
        action = request.POST.get('action')
        cf_name = get_site_id_cf_name()
        
        api_sites, api_error = self._get_api_data()
        if api_error:
            messages.error(request, f"Sync failed. Could not fetch external API data: {api_error}")
            return redirect('plugins:netbox_demandsite:demandsite_list')
            
        netbox_sites_map = {}
        for site in Site.objects.all():
            if site.custom_field_data:
                site_id_val = site.custom_field_data.get(cf_name)
                if site_id_val:
                    netbox_sites_map[str(site_id_val).strip().upper()] = site
                    
        sync_count = 0
        
        if action == 'sync_single':
            siteid = request.POST.get('siteid')
            api_site = next((x for x in api_sites if str(x.get('siteid')).strip().upper() == str(siteid).strip().upper()), None)
            netbox_site = netbox_sites_map.get(str(siteid).strip().upper())
            
            if api_site and netbox_site:
                if sync_one_site(netbox_site, api_site, cf_name):
                    messages.success(request, f"Successfully synchronized all fields for {siteid} ({netbox_site.name}) to NetBox.")
                else:
                    messages.info(request, f"Site {siteid} ({netbox_site.name}) is already fully synchronized.")
                return redirect(f"/plugins/demandsite/?site_id={siteid}")
            else:
                messages.error(request, f"Failed to sync site {siteid}. Make sure it exists in both systems.")
                
        elif action == 'sync_all':
            for api_site in api_sites:
                siteid = api_site.get('siteid')
                netbox_site = netbox_sites_map.get(str(siteid).strip().upper())
                if netbox_site:
                    if sync_one_site(netbox_site, api_site, cf_name):
                        sync_count += 1
            if sync_count > 0:
                messages.success(request, f"Successfully synchronized {sync_count} sites.")
            else:
                messages.info(request, "All matched sites are already synchronized.")
                
        return redirect('plugins:netbox_demandsite:demandsite_list')


class DemandsiteDetailView(LoginRequiredMixin, View):
    """
    Detail page for backward compatibility and direct site routing.
    Redirects back to main view with the site_id query parameter set.
    """
    def get(self, request, siteid):
        return redirect(f"/plugins/demandsite/?site_id={siteid}")

    def post(self, request, siteid):
        cf_name = get_site_id_cf_name()
        api_sites, _ = DemandsiteListView()._get_api_data()
        api_site = next((x for x in api_sites if str(x.get('siteid')).strip().upper() == str(siteid).strip().upper()), None)
        
        netbox_site = None
        for s in Site.objects.all():
            if s.custom_field_data:
                val = s.custom_field_data.get(cf_name)
                if val and str(val).strip().upper() == str(siteid).strip().upper():
                    netbox_site = s
                    break
        
        if api_site and netbox_site:
            if sync_one_site(netbox_site, api_site, cf_name):
                messages.success(request, f"Successfully synchronized all fields for {siteid} ({netbox_site.name}) to NetBox.")
            else:
                messages.info(request, f"Site {siteid} ({netbox_site.name}) is already fully synchronized.")
        return redirect(f"/plugins/demandsite/?site_id={siteid}")
