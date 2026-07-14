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

def build_cf_choices_map():
    """
    Builds a dictionary mapping (field_name, raw_value) -> display_label
    for all custom field choices in NetBox.
    """
    choices_map = {}
    for cf in CustomField.objects.all():
        if cf.choice_set:
            # 1. Parse extra_choices
            extra = getattr(cf.choice_set, 'extra_choices', None)
            if extra and isinstance(extra, list):
                for item in extra:
                    if isinstance(item, dict):
                        val = item.get('value')
                        label = item.get('label')
                        if val is not None and label is not None:
                            choices_map[(cf.name.lower(), str(val))] = label
                    elif isinstance(item, (list, tuple)) and len(item) >= 2:
                        choices_map[(cf.name.lower(), str(item[0]))] = item[1]
                    elif isinstance(item, str):
                        choices_map[(cf.name.lower(), item)] = item
            
            # 2. Parse choices relation if available
            choices_rel = getattr(cf.choice_set, 'choices', None)
            if choices_rel and hasattr(choices_rel, 'all'):
                try:
                    for choice_obj in choices_rel.all():
                        val = getattr(choice_obj, 'value', None)
                        label = getattr(choice_obj, 'label', None)
                        if val is not None and label is not None:
                            choices_map[(cf.name.lower(), str(val))] = label
                except Exception:
                    pass
    return choices_map

def resolve_cf_display(cf_name, val, choices_map):
    """
    Returns the human-readable display label for a custom field choice.
    Defaults to the raw value if no match is found.
    """
    if val is None:
        return '—'
    label = choices_map.get((cf_name.lower(), str(val)))
    if label:
        return label
    return str(val)

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
    
    # Selection fields require finding the correct choice key corresponding to the label
    choices_map = build_cf_choices_map()
    
    def get_choice_key_for_label(cf_name, label):
        # Look for the choice key corresponding to the display label
        for (name, key), lbl in choices_map.items():
            if name.lower() == cf_name.lower() and str(lbl).strip().upper() == str(label).strip().upper():
                return key
        return label # fallback to direct value if no matching selection choice is found

    if district_key and api_site.get('district'):
        val = api_site.get('district')
        choice_key = get_choice_key_for_label(district_key, val)
        if netbox_site.custom_field_data.get(district_key) != choice_key:
            netbox_site.custom_field_data[district_key] = choice_key
            updated = True
            
    if local_level_name_key and api_site.get('palika'):
        val = api_site.get('palika')
        choice_key = get_choice_key_for_label(local_level_name_key, val)
        if netbox_site.custom_field_data.get(local_level_name_key) != choice_key:
            netbox_site.custom_field_data[local_level_name_key] = choice_key
            updated = True
            
    if local_level_type_key and api_site.get('palika_type'):
        val = api_site.get('palika_type')
        mapping = {
            'RuralMunicipality': 'Rural Municipality',
            'Municipality': 'Municipality',
            'Metropolitan': 'Metropolitan',
            'SubMetropolitan': 'Sub-Metropolitan',
            'Sub-Metropolitan': 'Sub-Metropolitan',
        }
        val = mapping.get(val, val)
        choice_key = get_choice_key_for_label(local_level_type_key, val)
        if netbox_site.custom_field_data.get(local_level_type_key) != choice_key:
            netbox_site.custom_field_data[local_level_type_key] = choice_key
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
        choices_map = build_cf_choices_map()
        
        # Build mapping of NetBox sites by Site ID custom field (case-insensitive)
        netbox_sites_map = {}
        for site in Site.objects.select_related('region'):
            if site.custom_field_data:
                site_id_val = site.custom_field_data.get(cf_name)
                if site_id_val:
                    netbox_sites_map[str(site_id_val).strip().upper()] = site
                    
        # Calculate stats
        total_api_sites = len(api_sites)
        total_netbox = len(netbox_sites_map) # total linked sites
        
        # Search query filter from search box
        q = request.GET.get('q', '').strip().upper()
        
        correlated_sites = []
        total_mismatch = 0
        
        import re
        
        for item in api_sites:
            siteid = item.get('siteid', '')
            matched_site = netbox_sites_map.get(str(siteid).strip().upper())
            
            # Format API technologies
            tech_list = []
            for tech in item.get('operational_technologies', []):
                tech_name = tech.get('technology', '')
                m = re.search(r'([2345]G)', tech_name)
                if m:
                    tech_list.append(m.group(1))
                else:
                    tech_list.append(tech_name)
            if not tech_list and item.get('technology'):
                m = re.search(r'([2345]G)', item.get('technology'))
                if m:
                    tech_list.append(m.group(1))
                else:
                    tech_list.append(item.get('technology'))
            api_techs = ",".join(sorted(list(set(tech_list))))
            
            api_data = {
                'siteid': siteid,
                'sitename': item.get('sitename2') or item.get('sitename1') or '—',
                'province': item.get('province') or '—',
                'district': item.get('district') or '—',
                'palika': item.get('palika') or '—',
                'palika_type': item.get('palika_type') or '—',
                'wardno': item.get('wardno') if item.get('wardno') is not None else '—',
                'latitude': item.get('latitude') or '—',
                'longitude': item.get('longitude') or '—',
                'status': item.get('status') or '—',
                'technologies': api_techs,
            }
            
            nb_data = {
                'site_id': '—',
                'name': '—',
                'region': '—',
                'district': '—',
                'local_level_name': '—',
                'local_level': '—',
                'ward': '—',
                'latitude': '—',
                'longitude': '—',
                'status': '—',
                'devices': '—',
            }
            
            has_mismatch = False
            needs_sync = False
            
            if not matched_site:
                has_mismatch = True
            else:
                district_key = get_cf_key(matched_site, ['district'])
                local_level_name_key = get_cf_key(matched_site, ['local', 'level', 'name']) or get_cf_key(matched_site, ['palika'])
                local_level_type_key = get_cf_key(matched_site, ['local', 'level']) or get_cf_key(matched_site, ['palika', 'type'])
                if local_level_type_key == local_level_name_key:
                    local_level_type_key = None
                ward_key = get_cf_key(matched_site, ['ward'])
                
                nb_data = {
                    'site_id': resolve_cf_display(cf_name, matched_site.custom_field_data.get(cf_name), choices_map),
                    'name': matched_site.name,
                    'region': matched_site.region.name if matched_site.region else '—',
                    'district': resolve_cf_display(district_key, matched_site.custom_field_data.get(district_key), choices_map) if district_key else '—',
                    'local_level_name': resolve_cf_display(local_level_name_key, matched_site.custom_field_data.get(local_level_name_key), choices_map) if local_level_name_key else '—',
                    'local_level': resolve_cf_display(local_level_type_key, matched_site.custom_field_data.get(local_level_type_key), choices_map) if local_level_type_key else '—',
                    'ward': resolve_cf_display(ward_key, matched_site.custom_field_data.get(ward_key), choices_map) if ward_key else '—',
                    'latitude': matched_site.latitude if matched_site.latitude is not None else '—',
                    'longitude': matched_site.longitude if matched_site.longitude is not None else '—',
                    'status': matched_site.get_status_display() if hasattr(matched_site, 'get_status_display') else str(matched_site.status),
                    'devices': '—',
                }
                
                # Check mismatch comparing resolved display labels
                lat_diff = False
                lon_diff = False
                status_diff = False
                cf_diff = False
                
                api_lat = item.get('latitude')
                api_lon = item.get('longitude')
                
                if item.get('status') == 'Operational' and matched_site.status != 'active':
                    status_diff = True
                elif item.get('status') == 'Planned' and matched_site.status != 'planned':
                    status_diff = True
                    
                if api_lat:
                    try:
                        if not matched_site.latitude or abs(float(matched_site.latitude) - float(api_lat)) > 0.00001:
                            lat_diff = True
                    except Exception:
                        pass
                if api_lon:
                    try:
                        if not matched_site.longitude or abs(float(matched_site.longitude) - float(api_lon)) > 0.00001:
                            lon_diff = True
                    except Exception:
                        pass
                        
                if district_key and item.get('district') and nb_data['district'] != item.get('district'):
                    cf_diff = True
                if local_level_name_key and item.get('palika') and nb_data['local_level_name'] != item.get('palika'):
                    cf_diff = True
                if local_level_type_key and item.get('palika_type'):
                    val = item.get('palika_type')
                    mapping = {
                        'RuralMunicipality': 'Rural Municipality',
                        'Municipality': 'Municipality',
                        'Metropolitan': 'Metropolitan',
                        'SubMetropolitan': 'Sub-Metropolitan',
                        'Sub-Metropolitan': 'Sub-Metropolitan',
                    }
                    val = mapping.get(val, val)
                    if nb_data['local_level'] != val:
                        cf_diff = True
                if ward_key and item.get('wardno') is not None:
                    if str(nb_data['ward']) != str(item.get('wardno')):
                        cf_diff = True
                        
                if lat_diff or lon_diff or status_diff or cf_diff:
                    has_mismatch = True
                    needs_sync = True
            
            if has_mismatch:
                total_mismatch += 1
                
            # Filter search query
            if q:
                site_name1 = item.get('sitename1', '').upper()
                site_name2 = item.get('sitename2', '').upper()
                province = item.get('province', '').upper()
                district = item.get('district', '').upper()
                palika = item.get('palika', '').upper()
                if (q not in str(siteid).upper() and 
                    q not in site_name1 and 
                    q not in site_name2 and 
                    q not in province and 
                    q not in district and 
                    q not in palika):
                    continue
                    
            correlated_sites.append({
                'api_data': api_data,
                'nb_data': nb_data,
                'netbox_site': matched_site,
                'has_mismatch': has_mismatch,
                'needs_sync': needs_sync,
            })
            
        # Pagination
        from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
        paginator = Paginator(correlated_sites, 50)  # 50 items per page
        page_num = request.GET.get('page', 1)
        try:
            paginated_sites = paginator.page(page_num)
        except PageNotAnInteger:
            paginated_sites = paginator.page(1)
        except EmptyPage:
            paginated_sites = paginator.page(paginator.num_pages)

        # Bulk pre-fetch devices ONLY for the 50 sites on this page
        page_site_ids = [item['netbox_site'].id for item in paginated_sites if item['netbox_site']]
        if page_site_ids:
            devices = Device.objects.filter(site_id__in=page_site_ids)
            
            from collections import defaultdict
            site_devices_map = defaultdict(list)
            for d in devices:
                site_devices_map[d.site_id].append(d)
                
            for item in paginated_sites:
                nb_site = item['netbox_site']
                if nb_site and nb_site.id in site_devices_map:
                    dev_names = []
                    for d in site_devices_map[nb_site.id]:
                        role_obj = getattr(d, 'role', None) or getattr(d, 'device_role', None)
                        if role_obj and str(role_obj.name).strip() in ['WSD/BTS/2G', 'WSD/BTS/3G', 'WSD/BTS/4G']:
                            dev_names.append(d.name)
                    if dev_names:
                        item['nb_data']['devices'] = ",".join(dev_names)

        context = {
            'correlated_sites': paginated_sites,
            'paginator': paginator,
            'total_api_sites': total_api_sites,
            'total_netbox': total_netbox,
            'total_mismatch': total_mismatch,
            'cf_name': cf_name,
            'q': request.GET.get('q', '')
        }
        return render(request, self.template_name, context)

    def post(self, request):
        action = request.POST.get('action')
        cf_name = get_site_id_cf_name()
        
        api_sites, api_error = self._get_api_data()
        if api_error:
            messages.error(request, f"Sync failed: {api_error}")
            return redirect('plugins:netbox_demandsite:demandsite_list')
            
        netbox_sites_map = {}
        for site in Site.objects.all():
            if site.custom_field_data:
                site_id_val = site.custom_field_data.get(cf_name)
                if site_id_val:
                    netbox_sites_map[str(site_id_val).strip().upper()] = site
                    
        if action == 'sync_single':
            siteid = request.POST.get('siteid')
            api_site = next((x for x in api_sites if str(x.get('siteid')).strip().upper() == str(siteid).strip().upper()), None)
            netbox_site = netbox_sites_map.get(str(siteid).strip().upper())
            
            if api_site and netbox_site:
                if sync_one_site(netbox_site, api_site, cf_name):
                    messages.success(request, f"Successfully synchronized all fields for {siteid} ({netbox_site.name}) to NetBox.")
                else:
                    messages.info(request, f"Site {siteid} ({netbox_site.name}) is already fully synchronized.")
            else:
                messages.error(request, f"Failed to sync site {siteid}. Make sure it exists in both systems.")
                
        return redirect('plugins:netbox_demandsite:demandsite_list')


class DemandsiteDetailView(LoginRequiredMixin, View):
    """
    Detail page redirects to list.
    """
    def get(self, request, siteid):
        return redirect('plugins:netbox_demandsite:demandsite_list')

    def post(self, request, siteid):
        return redirect('plugins:netbox_demandsite:demandsite_list')
