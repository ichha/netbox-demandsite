import logging
import requests
import re
from decimal import Decimal
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.utils.text import slugify
from dcim.models import Site, Device, Region, DeviceType, Manufacturer
from extras.models import CustomField
from django.contrib.contenttypes.models import ContentType

logger = logging.getLogger('netbox.plugins.netbox_demandsite')

def clean_palika_name(name):
    if not name or name == '—':
        return ''
    name_str = str(name).strip()
    # Suffixes to strip case-insensitively
    suffixes = [
        "Mahanagarpalika", "Mahanagarpalika",
        "Submahanagarpalika", "Submahanagarpalika",
        "Nagarpalika", "Nagarpalika",
        "Gaupalika", "Rural Municipality", "Municipality",
        "VDC"
    ]
    for suffix in suffixes:
        suffix_len = len(suffix)
        if len(name_str) > suffix_len and name_str.lower().endswith(suffix.lower()):
            name_str = name_str[:-suffix_len].strip()
            # Also clean trailing underscores or spaces
            name_str = name_str.rstrip('_').strip()
            break
    return name_str
def clean_province_name(name):
    if not name or name == '—':
        return ''
    name_str = str(name).strip()
    if name_str.lower() == 'sudurpaschim':
        return 'Sudurpashchim'
    return name_str
def get_cf_key(site, keywords):
    """
    Finds a custom field name registered for the Site model
    that contains all the specified keywords (case-insensitive).
    """
    site_ct = ContentType.objects.get_for_model(Site)
    try:
        cf_fields = CustomField.objects.filter(object_types=site_ct)
        for cf in cf_fields:
            cf_name_lower = cf.name.lower()
            if all(kw in cf_name_lower for kw in keywords):
                return cf.name
    except Exception:
        pass
    try:
        cf_fields = CustomField.objects.filter(content_types=site_ct)
        for cf in cf_fields:
            cf_name_lower = cf.name.lower()
            if all(kw in cf_name_lower for kw in keywords):
                return cf.name
    except Exception:
        pass
        
    if site and site.custom_field_data:
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

try:
    from dcim.models import DeviceRole
except ImportError:
    from extras.models import DeviceRole

def get_or_create_tenant_and_group():
    try:
        from tenancy.models import Tenant, TenantGroup
    except ImportError:
        return None

    # Get or create TenantGroup "Nepal Telecom"
    group = TenantGroup.objects.filter(name="Nepal Telecom").first()
    if not group:
        try:
            group = TenantGroup.objects.create(
                name="Nepal Telecom",
                slug=slugify("Nepal Telecom")
            )
        except Exception as e:
            logger.error(f"Error creating TenantGroup Nepal Telecom: {e}")
            group = None

    # Get or create Tenant "WSD" under "Nepal Telecom" group
    tenant = Tenant.objects.filter(name="WSD").first()
    if not tenant:
        try:
            tenant = Tenant.objects.create(
                name="WSD",
                slug=slugify("WSD"),
                group=group
            )
        except Exception as e:
            logger.error(f"Error creating Tenant WSD: {e}")
            tenant = None
    else:
        # If tenant exists but group is not set, set it
        if group and tenant.group != group:
            try:
                tenant.group = group
                tenant.save()
            except Exception as e:
                logger.error(f"Error updating Tenant WSD group: {e}")
                
    return tenant

def get_or_create_device_role(name, color="9e9e9e"):
    role = DeviceRole.objects.filter(name=name).first()
    if not role:
        role = DeviceRole(name=name, slug=slugify(name), color=color)
        role.save()
    return role

def get_or_create_manufacturer(name):
    mfg = Manufacturer.objects.filter(name=name).first()
    if not mfg:
        mfg = Manufacturer(name=name, slug=slugify(name))
        mfg.save()
    return mfg

def get_or_create_device_type(model_name, manufacturer):
    dt = DeviceType.objects.filter(model=model_name, manufacturer=manufacturer).first()
    if not dt:
        dt = DeviceType(model=model_name, slug=slugify(model_name), manufacturer=manufacturer)
        dt.save()
    return dt

def create_netbox_device(name, site, role, dtype):
    tenant = get_or_create_tenant_and_group()
    try:
        Device.objects.create(
            name=name,
            site=site,
            role=role,
            device_type=dtype,
            status='active',
            tenant=tenant
        )
    except Exception:
        Device.objects.create(
            name=name,
            site=site,
            device_role=role,
            device_type=dtype,
            status='active',
            tenant=tenant
        )

# Maps device name suffix → (tech_flag_attr, role_name, device_type_model, role_color)
DEVICE_TECH_MAP = [
    ('_G', '2g', 'WSD/BTS/2G', 'GSM 2G',  '4caf50'),
    ('_U', '3g', 'WSD/BTS/3G', 'UMTS 3G', '2196f3'),
    ('_L', '4g', 'WSD/BTS/4G', 'LTE 4G',  'ff9800'),
]

def parse_api_technologies(api_site):
    """Returns a dict with keys '2g','3g','4g' set True if that tech is in API.
    Reads from 'operational_technologies' (list of dicts) or fallback 'technology' string.
    """
    result = {'2g': False, '3g': False, '4g': False}

    # Primary: operational_technologies is a list of {'technology': 'GSM 2G', ...}
    op_techs = api_site.get('operational_technologies', [])
    tech_strings = []
    if op_techs and isinstance(op_techs, list):
        for entry in op_techs:
            if isinstance(entry, dict):
                t = entry.get('technology', '')
            else:
                t = str(entry)
            tech_strings.append(t.upper())
    
    # Fallback: plain 'technology' string or 'technologies' list
    if not tech_strings:
        raw = api_site.get('technologies') or api_site.get('technology')
        if raw:
            if isinstance(raw, list):
                tech_strings = [str(t).upper() for t in raw]
            else:
                tech_strings = [str(raw).upper()]

    for t in tech_strings:
        if '2G' in t:
            result['2g'] = True
        if '3G' in t:
            result['3g'] = True
        if '4G' in t:
            result['4g'] = True

    return result

def find_best_site_match(site_list, api_name):
    if not site_list:
        return None
    if len(site_list) == 1:
        return site_list[0]
        
    api_name_upper = str(api_name).upper().strip()
    
    # 1. Exact name match (case-insensitive)
    for s in site_list:
        if s.name.upper().strip() == api_name_upper:
            return s
            
    # 2. Name starts with API name or API name starts with site name
    for s in site_list:
        s_name_upper = s.name.upper().strip()
        if s_name_upper.startswith(api_name_upper) or api_name_upper.startswith(s_name_upper):
            return s
            
    # 3. Name contains API name or API name contains site name
    for s in site_list:
        s_name_upper = s.name.upper().strip()
        if api_name_upper in s_name_upper or s_name_upper in api_name_upper:
            return s
            
    # 4. Fallback to first one
    return site_list[0]

def sync_devices_for_site(netbox_site, api_site):
    """
    Synchronises BTS devices for a NetBox site based on API technology flags.
    - Tech present in API  → device must exist and be Active.
    - Tech absent from API → device must exist and be Offline.
    Creates missing devices when tech is present.
    Returns a list of debug log strings.
    """
    logs = []
    techs = parse_api_technologies(api_site)
    siteid  = api_site.get('siteid') or ''
    # Match the display expected name logic exactly:
    api_sitename = api_site.get('sitename2') or api_site.get('sitename1') or api_site.get('sitename') or netbox_site.name or ''
    
    logs.append(f"siteid={siteid}, api_sitename={api_sitename}, techs={techs}")
    
    mfg = get_or_create_manufacturer("Huawei Technologies Co. Ltd.")
    tenant = get_or_create_tenant_and_group()
    logs.append(f"mfg={mfg.name if mfg else 'None'}, tenant={tenant.name if tenant else 'None'}")

    for suffix, tech_key, role_name, model_name, role_color in DEVICE_TECH_MAP:
        dev_name = f"{siteid}_{api_sitename}{suffix}"
        tech_present = techs[tech_key]

        # Prefix and suffix match to support variations in sitename spelling/format
        existing = Device.objects.filter(
            site=netbox_site,
            name__istartswith=f"{siteid}_",
            name__iendswith=suffix
        ).first()
        logs.append(f"dev_name={dev_name}, present={tech_present}, existing={existing.name if existing else 'None'}")

        if tech_present:
            if existing is None:
                # Create new active device
                role  = get_or_create_device_role(role_name, color=role_color)
                dtype = get_or_create_device_type(model_name, mfg)
                logs.append(f"Creating device {dev_name} (role={role_name}, type={model_name})")
                create_netbox_device(dev_name, netbox_site, role, dtype)
                logs.append(f"Device {dev_name} created.")
            else:
                # Re-activate device & set tenant
                changed = False
                if existing.status != 'active':
                    existing.status = 'active'
                    changed = True
                    logs.append(f"Device {existing.name} status changed to active.")
                if tenant and existing.tenant != tenant:
                    existing.tenant = tenant
                    changed = True
                    logs.append(f"Device {existing.name} tenant changed to {tenant.name}.")
                if changed:
                    existing.save()
        else:
            if existing is not None:
                # Tech gone from API → mark offline & set tenant
                changed = False
                if existing.status != 'offline':
                    existing.status = 'offline'
                    changed = True
                    logs.append(f"Device {existing.name} status changed to offline.")
                if tenant and existing.tenant != tenant:
                    existing.tenant = tenant
                    changed = True
                    logs.append(f"Device {existing.name} tenant changed to {tenant.name}.")
                if changed:
                    existing.save()
    return logs

def sync_one_site(netbox_site, api_site, cf_name):
    """
    Synchronizes standard fields and all custom fields (District, Palika, Ward)
    from external API site data to a NetBox site instance.
    """
    updated = False
    logs = []
    siteid = api_site.get('siteid') or ''
    
    # 1. Sync Coordinates (Latitude / Longitude)
    api_lat = api_site.get('latitude')
    api_lon = api_site.get('longitude')
    if api_lat:
        try:
            dec_lat = Decimal(str(api_lat).strip())
            if netbox_site.latitude != dec_lat:
                netbox_site.latitude = dec_lat
                updated = True
                logs.append(f"Updated lat to {dec_lat}")
        except Exception:
            pass
    if api_lon:
        try:
            dec_lon = Decimal(str(api_lon).strip())
            if netbox_site.longitude != dec_lon:
                netbox_site.longitude = dec_lon
                updated = True
                logs.append(f"Updated lon to {dec_lon}")
        except Exception:
            pass
            
    # 2. Sync Status
    api_status = api_site.get('status')
    if api_status == 'Operational' and netbox_site.status != 'active':
        netbox_site.status = 'active'
        updated = True
        logs.append("Updated status to active")
    elif api_status == 'Planned' and netbox_site.status != 'planned':
        netbox_site.status = 'planned'
        updated = True
        logs.append("Updated status to planned")
    elif api_status == 'Discontinued' and netbox_site.status != 'decommissioning':
        netbox_site.status = 'decommissioning'
        updated = True
        logs.append("Updated status to decommissioning")

    # 2.2. Sync Site Name from API
    api_name = api_site.get('sitename') or api_site.get('sitename2') or api_site.get('sitename1')
    if api_name and api_name != '—':
        if netbox_site.name != api_name:
            target_name = api_name
            # Check if name is already taken by a DIFFERENT site
            if Site.objects.filter(name=target_name).exclude(id=netbox_site.id).exists():
                target_name = f"{api_name} ({siteid})"[:100]
            counter = 1
            while Site.objects.filter(name=target_name).exclude(id=netbox_site.id).exists():
                target_name = f"{api_name} ({siteid}) {counter}"[:100]
                counter += 1
                
            if netbox_site.name != target_name:
                logs.append(f"Changing site name from {netbox_site.name} to {target_name}")
                netbox_site.name = target_name
                updated = True

    # 2.5. Sync Region/Province
    api_province = clean_province_name(api_site.get('province'))
    if api_province:
        region_obj = None
        is_ktm_bagmati = str(siteid).upper().startswith("KTM") and str(api_province).strip().lower() == "bagmati"
        if is_ktm_bagmati:
            region_obj = Region.objects.filter(name__iexact="Bagmati_KTM").first() or Region.objects.filter(name__iexact="Bagmati KTM").first()
            
        if not region_obj:
            region_obj = Region.objects.filter(name__iexact=api_province).first()
            
        if region_obj and netbox_site.region != region_obj:
            netbox_site.region = region_obj
            updated = True
            logs.append(f"Updated region to {region_obj.name}")
        
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
        logs.append(f"Updated description to {new_desc}")

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
        for (name, key), lbl in choices_map.items():
            if name.lower() == cf_name.lower() and str(lbl).strip().upper() == str(label).strip().upper():
                return key
        return label

    if district_key and api_site.get('district'):
        val = api_site.get('district')
        choice_key = get_choice_key_for_label(district_key, val)
        if netbox_site.custom_field_data.get(district_key) != choice_key:
            netbox_site.custom_field_data[district_key] = choice_key
            updated = True
            logs.append(f"Updated CF district to {val}")
            
    if local_level_name_key and api_site.get('palika'):
        val = api_site.get('palika')
        val_clean = clean_palika_name(val)
        choice_key = get_choice_key_for_label(local_level_name_key, val_clean)
        
        api_palika_first = val_clean.split()[0] if val_clean else ''
        existing_val = netbox_site.custom_field_data.get(local_level_name_key)
        resolved_existing = resolve_cf_display(local_level_name_key, existing_val, choices_map)
        nb_palika_first = str(resolved_existing).strip().split()[0] if resolved_existing else ''
        
        if api_palika_first.lower() != nb_palika_first.lower():
            netbox_site.custom_field_data[local_level_name_key] = choice_key
            updated = True
            logs.append(f"Updated CF local level name to {val_clean}")
            
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
            logs.append(f"Updated CF local level type to {val}")
            
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
                logs.append(f"Updated CF ward to {val}")
        except Exception:
            pass
            
    if updated:
        netbox_site.save()
        
    device_logs = sync_devices_for_site(netbox_site, api_site)
    logs.extend(device_logs)
    return updated, logs


class DemandsiteListView(LoginRequiredMixin, View):
    template_name = 'netbox_demandsite/demandsite_list.html'

    def _get_api_data(self):
        from django.core.cache import cache
        cache_key = "demandsite_api_data"
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return cached_data, None

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
            data = response.json()
            # Cache for 10 minutes
            cache.set(cache_key, data, 600)
            return data, None
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
        # Groups multiple sites under the same key if duplicates exist
        netbox_sites_map = {}
        for site in Site.objects.select_related('region'):
            if site.custom_field_data:
                site_id_val = site.custom_field_data.get(cf_name)
                if site_id_val:
                    key = str(site_id_val).strip().upper()
                    if key not in netbox_sites_map:
                        netbox_sites_map[key] = []
                    netbox_sites_map[key].append(site)
                    
        # Calculate stats
        total_api_sites = len(api_sites)
        total_netbox = len(netbox_sites_map) # total linked sites
        
        # Search query filter from search box
        q = request.GET.get('q', '').strip().upper()
        
        # Resolve custom field names once globally for the request to avoid N+1 queries inside the loop
        site_ct = ContentType.objects.get_for_model(Site)
        try:
            cf_fields = list(CustomField.objects.filter(object_types=site_ct))
        except Exception:
            cf_fields = list(CustomField.objects.filter(content_types=site_ct))
            
        district_key = None
        local_level_name_key = None
        local_level_type_key = None
        ward_key = None
        
        for cf in cf_fields:
            cf_name_lower = cf.name.lower()
            if all(kw in cf_name_lower for kw in ['district']):
                district_key = cf.name
            if all(kw in cf_name_lower for kw in ['local', 'level', 'name']) or all(kw in cf_name_lower for kw in ['palika']):
                if not ('type' in cf_name_lower or ('level' in cf_name_lower and not 'name' in cf_name_lower)):
                    local_level_name_key = cf.name
            if all(kw in cf_name_lower for kw in ['local', 'level']) or all(kw in cf_name_lower for kw in ['palika', 'type']):
                if 'type' in cf_name_lower or ('level' in cf_name_lower and not 'name' in cf_name_lower):
                    local_level_type_key = cf.name
            if all(kw in cf_name_lower for kw in ['ward']):
                ward_key = cf.name
                
        if local_level_type_key == local_level_name_key:
            local_level_type_key = None
            
        correlated_sites = []
        total_mismatch = 0
        total_matched = 0
        total_not_in_netbox = 0
        
        import re
        
        for item in api_sites:
            siteid = item.get('siteid', '')
            api_name = item.get('sitename2') or item.get('sitename1') or item.get('sitename') or ''
            
            site_list = netbox_sites_map.get(str(siteid).strip().upper(), [])
            matched_site = find_best_site_match(site_list, api_name)
            
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
            api_techs = sorted(list(set(tech_list)))
            
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
                'devices': [],
            }
            
            has_mismatch = False
            needs_sync = False
            region_diff = False
            palika_diff = False
            lat_diff = False
            lon_diff = False
            district_diff = False
            local_level_diff = False
            ward_diff = False
            status_diff = False
            name_diff = False
            tech_diff = False
            
            if not matched_site:
                has_mismatch = True
            else:
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
                    'devices': [],
                }
                
                # Check mismatch comparing resolved display labels
                lat_diff = False
                lon_diff = False
                status_diff = False
                cf_diff = False
                name_diff = False
                
                # Check site name mismatch
                api_name = item.get('sitename') or item.get('sitename2') or item.get('sitename1') or '—'
                if api_name and api_name != '—' and nb_data['name'] != api_name:
                    name_diff = True
                    cf_diff = True
                
                api_lat = item.get('latitude')
                api_lon = item.get('longitude')
                
                if item.get('status') == 'Operational' and matched_site.status != 'active':
                    status_diff = True
                elif item.get('status') == 'Planned' and matched_site.status != 'planned':
                    status_diff = True
                elif item.get('status') == 'Discontinued' and matched_site.status != 'decommissioning':
                    status_diff = True
                    
                if api_lat:
                    try:
                        if matched_site.latitude is None or abs(float(matched_site.latitude) - float(api_lat)) > 0.00001:
                            lat_diff = True
                    except Exception:
                        pass
                if api_lon:
                    try:
                        if matched_site.longitude is None or abs(float(matched_site.longitude) - float(api_lon)) > 0.00001:
                            lon_diff = True
                    except Exception:
                        pass
                        
                # Province/Region comparison with suffix normalization
                # e.g. NetBox region "Bagmati_KTM" matches API "Bagmati" if siteid starts with "KTM"
                region_diff = False
                api_province = clean_province_name(item.get('province', ''))
                nb_region = str(nb_data['region']).strip()
                if api_province and nb_region and nb_region != '—':
                    # Normalize: if nb_region has underscore suffix like "Bagmati_KTM",
                    # strip the suffix and check if siteid starts with it
                    nb_region_base = nb_region
                    if '_' in nb_region:
                        parts = nb_region.rsplit('_', 1)
                        suffix = parts[1]
                        if str(siteid).upper().startswith(suffix.upper()):
                            nb_region_base = parts[0]
                    elif ' ' in nb_region:
                        parts = nb_region.rsplit(' ', 1)
                        suffix = parts[1]
                        if str(siteid).upper().startswith(suffix.upper()):
                            nb_region_base = parts[0]
                    if api_province.lower() != nb_region_base.lower():
                        region_diff = True
                        cf_diff = True

                if district_key and item.get('district') and nb_data['district'] != item.get('district'):
                    district_diff = True
                    cf_diff = True
                # Local level name: compare first word of cleaned palika names
                palika_diff = False
                if local_level_name_key and item.get('palika'):
                    api_palika_clean = clean_palika_name(item.get('palika'))
                    nb_palika_clean = clean_palika_name(nb_data['local_level_name'])
                    api_palika_first = api_palika_clean.split()[0] if api_palika_clean else ''
                    nb_palika_first = nb_palika_clean.split()[0] if nb_palika_clean else ''
                    if api_palika_first.lower() != nb_palika_first.lower():
                        palika_diff = True
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
                        local_level_diff = True
                        cf_diff = True
                if ward_key and item.get('wardno') is not None:
                    if str(nb_data['ward']) != str(item.get('wardno')):
                        ward_diff = True
                        cf_diff = True
                        
                # Technology / device status mismatch check
                # Requires devices to be pre-fetched; we do a lightweight check here
                # using the site's pre-fetched devices (populated after pagination).
                # Full device status diff is detected below after device prefetch.

                if lat_diff or lon_diff or status_diff or cf_diff:
                    has_mismatch = True
                    needs_sync = True
            
            if matched_site:
                total_matched += 1
                if has_mismatch:
                    total_mismatch += 1
            else:
                total_not_in_netbox += 1
                
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
                'region_diff': region_diff,
                'palika_diff': palika_diff,
                'lat_diff': lat_diff,
                'lon_diff': lon_diff,
                'district_diff': district_diff,
                'local_level_diff': local_level_diff,
                'ward_diff': ward_diff,
                'status_diff': status_diff,
                'name_diff': name_diff,
                'tech_diff': False,  # resolved after device prefetch below
                '_api_techs': parse_api_technologies(item),
            })
            
        # Filter by card selection
        filter_type = request.GET.get('filter', '').strip().lower()
        if filter_type:
            filtered = []
            for item in correlated_sites:
                if filter_type == 'matched' and item['netbox_site'] is not None:
                    filtered.append(item)
                elif filter_type == 'not_in_netbox' and item['netbox_site'] is None:
                    filtered.append(item)
                elif filter_type == 'mismatched' and item['has_mismatch']:
                    filtered.append(item)
            correlated_sites = filtered

        # Pagination
        total_filtered_sites = len(correlated_sites)
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
            devices = Device.objects.select_related('role', 'tenant').filter(site_id__in=page_site_ids)
            
            from collections import defaultdict
            site_devices_map = defaultdict(list)
            for d in devices:
                site_devices_map[d.site_id].append(d)
                
            # suffix → tech key for diff detection
            SUFFIX_TECH = {'_G': '2g', '_U': '3g', '_L': '4g'}
            BTS_ROLES = {'WSD/BTS/2G', 'WSD/BTS/3G', 'WSD/BTS/4G'}

            for item in paginated_sites:
                nb_site = item['netbox_site']
                api_techs = item.get('_api_techs', {})
                tech_diff = False

                if nb_site and nb_site.id in site_devices_map:
                    dev_entries = []  # list of dicts: {name, status}
                    # Build name→device lookup for this site (using uppercase name for case-insensitive lookup)
                    name_to_dev = {}
                    for d in site_devices_map[nb_site.id]:
                        role_obj = getattr(d, 'role', None) or getattr(d, 'device_role', None)
                        if role_obj and str(role_obj.name).strip() in BTS_ROLES:
                            name_to_dev[d.name.upper()] = d
                            dev_entries.append({'name': d.name, 'status': d.status})

                    item['nb_data']['devices'] = dev_entries

                    # Check tech_diff: for each suffix, expected status vs actual
                    api_siteid   = item['api_data'].get('siteid', '')
                    tenant = get_or_create_tenant_and_group()
                    for suffix, tech_key in SUFFIX_TECH.items():
                        tech_present    = api_techs.get(tech_key, False)
                        
                        # Find existing device using prefix and suffix matching
                        existing_device = None
                        prefix_check = f"{api_siteid}_".upper()
                        suffix_check = suffix.upper()
                        for d in site_devices_map[nb_site.id]:
                            role_obj = getattr(d, 'role', None) or getattr(d, 'device_role', None)
                            if role_obj and str(role_obj.name).strip() in BTS_ROLES:
                                d_name_upper = d.name.upper()
                                if d_name_upper.startswith(prefix_check) and d_name_upper.endswith(suffix_check):
                                    existing_device = d
                                    break

                        if tech_present:
                            # Device should exist and be active, and tenant set to WSD
                            if existing_device is None or existing_device.status != 'active':
                                tech_diff = True
                            elif tenant and existing_device.tenant_id != tenant.id:
                                tech_diff = True
                        else:
                            # Device should not exist or should be offline, and tenant set to WSD
                            if existing_device is not None:
                                if existing_device.status != 'offline':
                                    tech_diff = True
                                elif tenant and existing_device.tenant_id != tenant.id:
                                    tech_diff = True
                else:
                    # No devices in NetBox at all — diff if API has any tech
                    if any(api_techs.values()):
                        tech_diff = True
                    item['nb_data']['devices'] = []

                item['tech_diff'] = tech_diff
                if tech_diff:
                    item['has_mismatch'] = True
                    item['needs_sync'] = True

        context = {
            'correlated_sites': paginated_sites,
            'paginator': paginator,
            'total_api_sites': total_api_sites,
            'total_netbox': total_netbox,
            'total_matched': total_matched,
            'total_not_in_netbox': total_not_in_netbox,
            'total_mismatch': total_mismatch,
            'total_filtered_sites': total_filtered_sites,
            'cf_name': cf_name,
            'q': request.GET.get('q', ''),
            'filter': filter_type,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        action = request.POST.get('action')
        cf_name = get_site_id_cf_name()
        
        # Read redirect parameters
        page = request.POST.get('page', '1')
        q = request.POST.get('q', '').strip()
        filter_type = request.POST.get('filter', '').strip()
        
        redirect_url = reverse('plugins:netbox_demandsite:demandsite_list')
        params = []
        if page and page != '1':
            params.append(f"page={page}")
        if q:
            params.append(f"q={q}")
        if filter_type:
            params.append(f"filter={filter_type}")
        if params:
            redirect_url = f"{redirect_url}?{'&'.join(params)}"

        api_sites, api_error = self._get_api_data()
        if api_error:
            messages.error(request, f"Sync failed: {api_error}")
            return redirect(redirect_url)
            
        if action == 'sync_single':
            siteid = request.POST.get('siteid')
            if not siteid:
                messages.error(request, "Failed to sync: Site ID not provided.")
                return redirect(redirect_url)
                
            api_site = next((x for x in api_sites if str(x.get('siteid')).strip().upper() == str(siteid).strip().upper()), None)
            
            # Lookup sites directly using JSONField query
            netbox_sites = list(Site.objects.filter(**{f"custom_field_data__{cf_name}": siteid.strip()}))
            if not netbox_sites:
                # Case-insensitive fallback
                netbox_sites = list(Site.objects.filter(**{f"custom_field_data__{cf_name}__iexact": siteid.strip()}))
            
            api_name = api_site.get('sitename') or api_site.get('sitename2') or api_site.get('sitename1') or ''
            netbox_site = find_best_site_match(netbox_sites, api_name)
            
            if api_site:
                try:
                    if not netbox_site:
                        # Create new site
                        sitename_raw = api_site.get('sitename2') or api_site.get('sitename1') or siteid
                        base_name = sitename_raw
                        name = base_name
                        slug = slugify(siteid)
                        
                        if Site.objects.filter(slug=slug).exists():
                            slug = slugify(f"{siteid}-{base_name}")[:100]
                        if Site.objects.filter(name=name).exists():
                            name = f"{base_name} ({siteid})"[:100]
                        
                        counter = 1
                        while Site.objects.filter(name=name).exists():
                            name = f"{base_name} ({siteid}) {counter}"[:100]
                            counter += 1
                            
                        netbox_site = Site(
                            name=name,
                            slug=slug,
                            status='active' if api_site.get('status') == 'Operational' else 'planned',
                            custom_field_data={cf_name: siteid}
                        )
                        netbox_site.save()
                        sync_one_site(netbox_site, api_site, cf_name)
                        messages.success(request, f"Successfully created and synchronized site {siteid} ({name}) in NetBox.")
                    else:
                        updated, logs_list = sync_one_site(netbox_site, api_site, cf_name)
                        if updated or any("Device" in log or "Creating" in log or "changed" in log or "created" in log for log in logs_list):
                            messages.success(request, f"Successfully synchronized all fields for {siteid} ({netbox_site.name}) to NetBox.")
                        else:
                            messages.success(request, f"Successfully synchronized all fields for {siteid} ({netbox_site.name}) to NetBox.")
                except Exception as e:
                    import traceback
                    logger.error(f"Sync error for site {siteid}: {traceback.format_exc()}")
                    messages.error(request, f"Sync error for site {siteid}: {str(e)}")
            else:
                messages.error(request, f"Failed to sync site {siteid}. Site not found in API data.")
                
        elif action == 'sync_selected':
            siteids = request.POST.getlist('selected_siteids')
            if not siteids:
                messages.error(request, "Failed to sync: No sites selected.")
                return redirect(redirect_url)
                
            success_count = 0
            errors = []
            for siteid in siteids:
                if not siteid:
                    continue
                api_site = next((x for x in api_sites if str(x.get('siteid')).strip().upper() == str(siteid).strip().upper()), None)
                if not api_site:
                    errors.append(f"Site {siteid} not found in API.")
                    continue
                    
                netbox_sites = list(Site.objects.filter(**{f"custom_field_data__{cf_name}": siteid.strip()}))
                if not netbox_sites:
                    netbox_sites = list(Site.objects.filter(**{f"custom_field_data__{cf_name}__iexact": siteid.strip()}))
                    
                api_name = api_site.get('sitename') or api_site.get('sitename2') or api_site.get('sitename1') or ''
                netbox_site = find_best_site_match(netbox_sites, api_name)
                
                try:
                    if not netbox_site:
                        # Create new site
                        sitename_raw = api_site.get('sitename2') or api_site.get('sitename1') or siteid
                        base_name = sitename_raw
                        name = base_name
                        slug = slugify(siteid)
                        
                        if Site.objects.filter(slug=slug).exists():
                            slug = slugify(f"{siteid}-{base_name}")[:100]
                        if Site.objects.filter(name=name).exists():
                            name = f"{base_name} ({siteid})"[:100]
                        
                        counter = 1
                        while Site.objects.filter(name=name).exists():
                            name = f"{base_name} ({siteid}) {counter}"[:100]
                            counter += 1
                            
                        netbox_site = Site(
                            name=name,
                            slug=slug,
                            status='active' if api_site.get('status') == 'Operational' else 'planned',
                            custom_field_data={cf_name: siteid}
                        )
                        netbox_site.save()
                        sync_one_site(netbox_site, api_site, cf_name)
                    else:
                        sync_one_site(netbox_site, api_site, cf_name)
                    success_count += 1
                except Exception as e:
                    import traceback
                    logger.error(f"Sync error for site {siteid}: {traceback.format_exc()}")
                    errors.append(f"Site {siteid}: {str(e)}")
                    
            if success_count > 0:
                messages.success(request, f"Successfully synchronized {success_count} sites.")
            if errors:
                messages.error(request, f"Failed to sync some sites: {', '.join(errors[:5])}")
                
        return redirect(redirect_url)


class DemandsiteDetailView(LoginRequiredMixin, View):
    """
    Detail page redirects to list.
    """
    def get(self, request, siteid):
        return redirect('plugins:netbox_demandsite:demandsite_list')

    def post(self, request, siteid):
        return redirect('plugins:netbox_demandsite:demandsite_list')
