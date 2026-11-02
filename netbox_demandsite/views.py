import logging
import requests
import re
from decimal import Decimal
from django.shortcuts import render, redirect
from django.urls import reverse
from django.http import JsonResponse
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
try:
    from netbox.views import PermissionRequiredMixin
except ImportError:
    from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib import messages
from django.utils.text import slugify
from dcim.models import Site, Device, Region, DeviceType, Manufacturer
from extras.models import CustomField
from django.contrib.contenttypes.models import ContentType

logger = logging.getLogger('netbox.plugins.netbox_demandsite')

def is_placeholder(val):
    """
    Checks if a value is empty or an API placeholder like Unknown, -, N/A, None, null, 0, 0.0.
    """
    if val is None:
        return True
    s = str(val).strip().lower()
    return s in ('', '—', '-', 'none', 'unknown', 'n/a', 'null', '0', '0.0')

def clean_palika_name(name):
    """
    Cleans palika/municipality name for matching and NetBox custom fields.
    Strips administrative suffixes like Upa-Mahanagarpalika, Mahanagarpalika,
    Nagarpalika, Gaupalika, Gaunpalika, Municipality, VDC, etc.
    Specifically for 'Janakpur Upa-Mahanagarpalika', returns 'Janakpur'.
    """
    if is_placeholder(name):
        return ''
    name_str = str(name).strip()
    
    # Special case: Janakpur Upa-Mahanagarpalika / Janakpurdham -> Janakpur
    if name_str.lower().startswith('janakpur'):
        return 'Janakpur'
        
    # 1. Remove Upa-Mahanagarpalika / Sub-Metropolitan variations first
    name_str = re.sub(r'(?i)\s*(upa[- ]?mahanagarpalika|sub[- ]?mahanagarpalika|sub[- ]?metropolitan|metropolitan)', '', name_str)
    # 2. Remove Mahanagarpalika, Nagarpalika, Gaupalika, Gaunpalika, Municipality, VDC, City
    name_str = re.sub(r'(?i)\s*(mahanagarpalika|nagarpalika|gaunpalika|gaupalika|rural\s+municipality|municipality|vdc|city)', '', name_str)
    # 3. Remove trailing upa, sub, hyphens, underscores
    name_str = re.sub(r'(?i)\s*(upa|sub)[-_ ]*$', '', name_str)
    name_str = name_str.strip('-_ ')
    
    return name_str

def clean_province_name(name):
    if is_placeholder(name):
        return ''
    name_str = str(name).strip()
    if name_str.lower() == 'sudurpaschim':
        return 'Sudurpashchim'
    return name_str

def get_api_sitename2(item):
    """
    Extracts sitename2 from Demand API item.
    Falls back to sitename1 or sitename if sitename2 is not provided.
    """
    if not item or not isinstance(item, dict):
        return ""
    val = item.get('sitename2')
    if not is_placeholder(val):
        return str(val).strip()
    val = item.get('sitename1') or item.get('sitename') or ""
    if not is_placeholder(val):
        return str(val).strip()
    return ""

def get_api_site_name(item):
    return get_api_sitename2(item)

def get_expected_netbox_site_name(siteid, sitename2):
    """
    Builds the expected NetBox Site name: Siteid_Sitename2.
    Cleans duplicate site IDs and leading/trailing whitespace/underscores.
    e.g. siteid='CDR1122', sitename2='GUJARA2' -> 'CDR1122_GUJARA2'
    e.g. siteid='KTM602', sitename2='KTM602' -> 'KTM602_KTM602'
    """
    if not siteid:
        return ""
    siteid_str = str(siteid).strip()
    if not sitename2:
        return siteid_str
    
    clean_sitename = str(sitename2).strip().lstrip('_').strip()
    if clean_sitename.upper().startswith(siteid_str.upper()):
        clean_sitename = clean_sitename[len(siteid_str):].lstrip('_- ')
    clean_sitename = clean_sitename.strip('_ ')
    
    if clean_sitename:
        return f"{siteid_str}_{clean_sitename}"
    else:
        # If sitename2 was identical to siteid, e.g. siteid=KTM602, sitename2=KTM602
        return f"{siteid_str}_{siteid_str}"

def is_site_name_match(nb_name, expected_name):
    """
    Compares NetBox site name with expected name, ignoring accidental spaces around underscores.
    e.g. 'KTM602 _KTM602' matches 'KTM602_KTM602'.
    """
    if not nb_name or not expected_name:
        return False
    norm_nb = str(nb_name).replace(' ', '').upper()
    norm_exp = str(expected_name).replace(' ', '').upper()
    return norm_nb == norm_exp
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
    
    # 1. Exact name match (case-insensitive) on site_name custom field
    for s in site_list:
        cf_name_val = s.custom_field_data.get('site_name') if s.custom_field_data else None
        if cf_name_val and str(cf_name_val).upper().strip() == api_name_upper:
            return s
            
    # 2. Name starts with API name or API name starts with site name on site_name custom field
    for s in site_list:
        cf_name_val = s.custom_field_data.get('site_name') if s.custom_field_data else None
        if cf_name_val:
            s_name_upper = str(cf_name_val).upper().strip()
            if s_name_upper.startswith(api_name_upper) or api_name_upper.startswith(s_name_upper):
                return s
                
    # 3. Name contains API name or API name contains site name on site_name custom field
    for s in site_list:
        cf_name_val = s.custom_field_data.get('site_name') if s.custom_field_data else None
        if cf_name_val:
            s_name_upper = str(cf_name_val).upper().strip()
            if api_name_upper in s_name_upper or s_name_upper in api_name_upper:
                return s
                
    # 4. Fallback to name check of the site model itself (just in case they are not migrated yet)
    for s in site_list:
        if s.name.upper().strip() == api_name_upper:
            return s
    for s in site_list:
        s_name_upper = s.name.upper().strip()
        if s_name_upper.startswith(api_name_upper) or api_name_upper.startswith(s_name_upper):
            return s
            
    # 5. Fallback to first one
    return site_list[0]

def find_netbox_site_for_api(siteid, api_name, cf_name):
    """
    Finds the existing NetBox Site matching the given siteid and api_name.
    Uses multi-strategy lookup:
    1. Direct JSON lookup on custom_field_data[cf_name]
    2. Case-insensitive in-memory match on custom_field_data[cf_name] across all sites
    3. Match by site name (e.g. "CDR807_...", "CDR807__...", or exact "CDR807")
    4. Match by slug
    """
    if not siteid:
        return None
    siteid_clean = str(siteid).strip().upper()
    
    # 1. Direct JSON lookup on cf_name
    try:
        sites = list(Site.objects.filter(**{f"custom_field_data__{cf_name}": siteid.strip()}))
        if sites:
            return find_best_site_match(sites, api_name)
    except Exception:
        pass

    # 2. In-memory check matching custom_field_data[cf_name]
    all_sites = list(Site.objects.select_related('region').all())
    matching_cf = []
    for s in all_sites:
        if s.custom_field_data:
            val = s.custom_field_data.get(cf_name)
            if val and str(val).strip().upper() == siteid_clean:
                matching_cf.append(s)
    if matching_cf:
        return find_best_site_match(matching_cf, api_name)
        
    # 3. Match by site name (e.g. "CDR807_...", "CDR807__...", or "CDR807")
    matching_name = [
        s for s in all_sites
        if s.name.upper() == siteid_clean or 
           s.name.upper().startswith(f"{siteid_clean}_") or
           s.name.upper().startswith(f"{siteid_clean} ")
    ]
    if matching_name:
        return find_best_site_match(matching_name, api_name)

    # 4. Match by slug
    slug_val = slugify(siteid_clean)
    if slug_val:
        matching_slug = [
            s for s in all_sites
            if s.slug and (s.slug == slug_val or s.slug.startswith(f"{slug_val}-") or s.slug.startswith(f"{slug_val}_"))
        ]
        if matching_slug:
            return find_best_site_match(matching_slug, api_name)

    return None

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
    siteid = api_site.get('siteid') or ''
    api_sitename = api_site.get('sitename2') or api_site.get('sitename1') or api_site.get('sitename') or netbox_site.name or ''
    
    # Use NetBox site's custom field 'site_name' if available, otherwise fallback to api_sitename
    nb_site_name = netbox_site.custom_field_data.get('site_name') if netbox_site.custom_field_data else None
    display_sitename = nb_site_name or api_sitename
    
    logs.append(f"siteid={siteid}, api_sitename={api_sitename}, nb_site_name={nb_site_name}, techs={techs}")
    
    mfg = get_or_create_manufacturer("Huawei Technologies Co. Ltd.")
    tenant = get_or_create_tenant_and_group()
    logs.append(f"mfg={mfg.name if mfg else 'None'}, tenant={tenant.name if tenant else 'None'}")

    for suffix, tech_key, role_name, model_name, role_color in DEVICE_TECH_MAP:
        dev_name = f"{siteid}_{display_sitename}{suffix}"
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
            # If present in NetBox and not in Demand, do NOT sync / do NOT touch.
            # Demand site is the main source: only sync technologies present in Demand.
            pass
    return logs

def sync_one_site(netbox_site, api_site, cf_name):
    """
    Synchronizes standard fields and all custom fields (District, Palika, Ward)
    from external API site data to a NetBox site instance.
    """
    updated = False
    logs = []
    siteid = str(api_site.get('siteid') or '').strip()
    
    if netbox_site.custom_field_data is None:
        netbox_site.custom_field_data = {}
        
    # 0. Sync Site ID Custom Field
    if cf_name and siteid:
        if str(netbox_site.custom_field_data.get(cf_name, '')).strip().upper() != siteid.upper():
            netbox_site.custom_field_data[cf_name] = siteid
            updated = True
            logs.append(f"Updated CF {cf_name} to {siteid}")

    # 1. Sync Coordinates (Latitude / Longitude)
    api_lat = api_site.get('latitude')
    api_lon = api_site.get('longitude')
    if not is_placeholder(api_lat):
        try:
            dec_lat = Decimal(str(api_lat).strip())
            if netbox_site.latitude is None or abs(float(netbox_site.latitude) - float(dec_lat)) > 0.00001:
                netbox_site.latitude = dec_lat
                updated = True
                logs.append(f"Updated lat to {dec_lat}")
        except Exception:
            pass
    if not is_placeholder(api_lon):
        try:
            dec_lon = Decimal(str(api_lon).strip())
            if netbox_site.longitude is None or abs(float(netbox_site.longitude) - float(dec_lon)) > 0.00001:
                netbox_site.longitude = dec_lon
                updated = True
                logs.append(f"Updated lon to {dec_lon}")
        except Exception:
            pass
            
    # 2. Sync Status
    api_status_str = str(api_site.get('status') or '').strip().lower()
    if api_status_str == 'operational':
        target_status = 'active'
    elif api_status_str == 'discontinued':
        target_status = 'decommissioning'
    else:
        target_status = 'planned'
        
    if netbox_site.status != target_status:
        netbox_site.status = target_status
        updated = True
        logs.append(f"Updated status to {target_status}")

    # 2.2. Sync NetBox Site name & description to Siteid_sitename, and custom field site_name to API sitename
    site_name_cf_key = get_cf_key(netbox_site, ['site', 'name']) or 'site_name'
    sitename2 = get_api_sitename2(api_site)
    if sitename2 and not is_placeholder(sitename2):
        if netbox_site.custom_field_data.get(site_name_cf_key) != sitename2:
            logs.append(f"Changing {site_name_cf_key} custom field from {netbox_site.custom_field_data.get(site_name_cf_key)} to {sitename2}")
            netbox_site.custom_field_data[site_name_cf_key] = sitename2
            updated = True

    desired_name = get_expected_netbox_site_name(siteid, sitename2)
    target_name = desired_name
    # Ensure unique site name in NetBox
    if Site.objects.filter(name=target_name).exclude(id=netbox_site.id).exists():
        target_name = f"{desired_name} ({netbox_site.id})"[:100]
    counter = 1
    while Site.objects.filter(name=target_name).exclude(id=netbox_site.id).exists():
        target_name = f"{desired_name} ({netbox_site.id}) {counter}"[:100]
        counter += 1
        
    if netbox_site.name != target_name:
        logs.append(f"Changing NetBox site name from {netbox_site.name} to {target_name}")
        netbox_site.name = target_name
        updated = True

    expected_slug = slugify(desired_name.strip().lower())
    if not expected_slug:
        expected_slug = slugify(siteid.strip().lower())
    if netbox_site.slug != expected_slug:
        target_slug = expected_slug
        if Site.objects.filter(slug=target_slug).exclude(id=netbox_site.id).exists():
            target_slug = f"{expected_slug}-{netbox_site.id}"[:100]
        counter = 1
        while Site.objects.filter(slug=target_slug).exclude(id=netbox_site.id).exists():
            target_slug = f"{expected_slug}-{netbox_site.id}-{counter}"[:100]
            counter += 1
        if netbox_site.slug != target_slug:
            logs.append(f"Changing slug from {netbox_site.slug} to {target_slug}")
            netbox_site.slug = target_slug
            updated = True

    if netbox_site.description != desired_name:
        netbox_site.description = desired_name
        updated = True
        logs.append(f"Updated description to {desired_name}")

    # 2.5. Sync Region/Province
    api_province = clean_province_name(api_site.get('province'))
    if api_province and api_province.lower() not in ('unknown', '—', '-', 'n/a', '', 'none'):
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

    # 4. Sync Custom Fields (District, Local Level Name, Local Level, Ward)
    district_key = get_cf_key(netbox_site, ['district'])
    local_level_name_key = get_cf_key(netbox_site, ['local', 'level', 'name']) or get_cf_key(netbox_site, ['palika'])
    local_level_type_key = get_cf_key(netbox_site, ['local', 'level']) or get_cf_key(netbox_site, ['palika', 'type'])
    if local_level_type_key == local_level_name_key:
        local_level_type_key = None
    ward_key = get_cf_key(netbox_site, ['ward'])
    
    choices_map = build_cf_choices_map()
    
    def get_choice_key_for_label(cf_field_name, label):
        if not label:
            return label
        label_str = str(label).strip().upper()
        clean_lbl = clean_palika_name(label).strip().upper()
        # 1. Exact match on label or key
        for (name, key), lbl in choices_map.items():
            if name.lower() == cf_field_name.lower():
                if str(lbl).strip().upper() == label_str or str(key).strip().upper() == label_str:
                    return key
        # 2. Match cleaned name
        if clean_lbl:
            for (name, key), lbl in choices_map.items():
                if name.lower() == cf_field_name.lower():
                    if clean_palika_name(str(lbl)).strip().upper() == clean_lbl:
                        return key
        # 3. First word match (e.g. "Bhagaha", "Janakpur")
        first_word = label_str.split()[0] if label_str else ''
        if len(first_word) >= 3:
            for (name, key), lbl in choices_map.items():
                if name.lower() == cf_field_name.lower():
                    cand_first = str(lbl).strip().upper().split()[0] if lbl else ''
                    if cand_first == first_word:
                        return key
        return label

    if district_key and api_site.get('district'):
        val = api_site.get('district')
        if str(val).strip().lower() not in ('unknown', '—', '-', 'n/a', '', 'none'):
            choice_key = get_choice_key_for_label(district_key, val)
            if netbox_site.custom_field_data.get(district_key) != choice_key:
                netbox_site.custom_field_data[district_key] = choice_key
                updated = True
                logs.append(f"Updated CF district to {val}")
            
    if local_level_name_key and api_site.get('palika'):
        val = api_site.get('palika')
        val_clean = clean_palika_name(val)
        if val_clean and val_clean.lower() not in ('unknown', '—', '-', 'n/a', '', 'none'):
            choice_key = get_choice_key_for_label(local_level_name_key, val_clean)
            if netbox_site.custom_field_data.get(local_level_name_key) != choice_key:
                netbox_site.custom_field_data[local_level_name_key] = choice_key
                updated = True
                logs.append(f"Updated CF local level name to {val_clean}")
            
    if local_level_type_key and api_site.get('palika_type'):
        val = api_site.get('palika_type')
        if str(val).strip().lower() not in ('unknown', '—', '-', 'n/a', '', 'none'):
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
        if str(val).strip() not in ('—', '-', '', 'None', 'Unknown'):
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
        netbox_site.custom_field_data = dict(netbox_site.custom_field_data)
        netbox_site.save()
        logs.append(f"Saved NetBox site: {netbox_site.name}")
        
    device_logs = sync_devices_for_site(netbox_site, api_site)
    logs.extend(device_logs)
    return updated, logs


def fetch_demand_api_data():
    from django.core.cache import cache
    cache_key = "demandsite_api_data"
    fallback_key = "demandsite_api_data_fallback"
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data, None, False

    url = "https://demandsite.ntc.net.np/api/share/site-dimension"
    from django.conf import settings
    plugin_config = settings.PLUGINS_CONFIG.get('netbox_demandsite', {})
    url = plugin_config.get('api_url', url)
    api_token = plugin_config.get('api_token', 'ds_share_7b4a2f8c1e9d3056bf47e382d61a9c8f')
    
    headers = {
        "Authorization": f"Bearer {api_token}"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        # Cache for 10 minutes
        cache.set(cache_key, data, 600)
        cache.set(fallback_key, data, 86400 * 7)
        return data, None, False
    except Exception as e:
        logger.error(f"Error fetching Demandsite data: {e}")
        fallback_data = cache.get(fallback_key)
        if fallback_data is not None:
            return fallback_data, f"API server is offline ({e}). Using cached data.", True
        return [], str(e), False


def auto_sync_missing_demand_sites(logger_func=None):
    """
    Automated 24-Hour Synchronization:
    Checks which sites exist in Demand API and do NOT exist in NetBox.
    If so, creates and synchronizes them into NetBox with basic site data and BTS devices.
    Sites that already exist in NetBox are left untouched.
    """
    from django.core.cache import cache
    from django.utils import timezone

    if logger_func is None:
        logger_func = logger.info

    now_str = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
    logger_func(f"[{now_str}] Starting 24-Hour Auto-Sync (Checking Demand sites missing in NetBox)...")

    api_sites, api_error, _ = fetch_demand_api_data()
    if api_error and not api_sites:
        err_msg = f"Auto-Sync failed: Could not fetch Demand Site API data ({api_error})"
        logger_func(err_msg)
        cache.set('demandsite_auto_sync_log', {
            'time': now_str,
            'summary': f"Failed: {api_error}",
            'created': 0,
            'skipped': 0,
            'logs': [err_msg],
            'status': 'error'
        }, 86400 * 3)
        return {'total': 0, 'created': 0, 'skipped': 0, 'logs': [err_msg], 'errors': [err_msg]}

    cf_name = get_site_id_cf_name()
    total = len(api_sites)
    created_count = 0
    skipped_count = 0
    sync_logs = []
    errors = []

    for item in api_sites:
        siteid = str(item.get('siteid') or '').strip()
        if not siteid:
            continue
        try:
            api_name = get_api_site_name(item)
            netbox_site = find_netbox_site_for_api(siteid, api_name, cf_name)

            # Auto sync rule: Only sync sites that exist in Demand and do NOT exist in NetBox!
            if not netbox_site:
                clean_sitename = get_api_sitename2(item)
                desired_name = get_expected_netbox_site_name(siteid, clean_sitename)
                name = desired_name
                slug = slugify(desired_name) or slugify(siteid)
                if Site.objects.filter(slug=slug).exists():
                    slug = slugify(f"{desired_name}-site")[:100]
                if Site.objects.filter(name=name).exists():
                    name = f"{desired_name} (new)"[:100]
                counter = 1
                while Site.objects.filter(name=name).exists():
                    name = f"{desired_name} {counter}"[:100]
                    counter += 1

                netbox_site = Site(
                    name=name,
                    slug=slug,
                    description=desired_name,
                    status='active' if str(item.get('status')).strip().lower() == 'operational' else 'planned',
                    custom_field_data={
                        cf_name: siteid,
                        'site_name': clean_sitename or siteid
                    }
                )
                netbox_site.save()
                _, site_logs = sync_one_site(netbox_site, item, cf_name)
                created_count += 1
                log_entry = f"Imported missing site: {siteid} ({netbox_site.name})"
                if site_logs:
                    log_entry += f" -> {'; '.join(site_logs[:3])}"
                logger_func(log_entry)
                sync_logs.append(log_entry)
            else:
                skipped_count += 1
        except Exception as e:
            err = f"Site {siteid}: {str(e)}"
            logger_func(f"Auto-Sync error: {err}")
            errors.append(err)
            sync_logs.append(f"ERROR on {siteid}: {str(e)}")

    summary_text = f"Checked {total} Demand sites: {created_count} missing sites imported into NetBox, {skipped_count} existing sites preserved."
    if errors:
        summary_text += f" ({len(errors)} errors)"
    logger_func(f"[{now_str}] Auto-Sync finished: {summary_text}")

    # Store last auto-sync result in cache for 7 days so UI can display it
    cache.set('demandsite_auto_sync_log', {
        'time': now_str,
        'summary': summary_text,
        'created': created_count,
        'skipped': skipped_count,
        'total': total,
        'logs': sync_logs,
        'status': 'success' if not errors else 'warning'
    }, 86400 * 7)

    return {
        'total': total,
        'created': created_count,
        'skipped': skipped_count,
        'logs': sync_logs,
        'errors': errors
    }


def sync_all_demand_sites(logger_func=None, target_siteid=None):
    """
    Synchronizes sites and BTS devices from Demand Site API into NetBox.
    Designed for:
      - 24-hour periodic background job (netbox.jobs.system_job / jobs.py)
      - NetBox Custom Script (scripts.py)
      - Django management command (manage.py sync_demandsite)
      - UI Bulk Action 'Sync All'
      
    Master logic:
      - Matches siteid with NetBox custom field 'site_id' or site name.
      - Updates coordinates, status, name, region, local level, ward, BTS devices.
      - Never overwrites existing NetBox data if Demand has placeholder/empty values.
      - Pushes basic site data for sites that exist in Demand and are not yet in NetBox.
    """
    if logger_func is None:
        logger_func = logger.info

    logger_func("Starting Demandsite 24-Hour Synchronization...")
    api_sites, api_error, _ = fetch_demand_api_data()
    if api_error and not api_sites:
        err_msg = f"Failed to fetch Demand Site API data: {api_error}"
        logger_func(err_msg)
        return {'total': 0, 'created': 0, 'updated': 0, 'unchanged': 0, 'errors': [err_msg]}

    if target_siteid:
        target_clean = str(target_siteid).strip().upper()
        api_sites = [x for x in api_sites if str(x.get('siteid') or '').strip().upper() == target_clean]

    cf_name = get_site_id_cf_name()
    total = len(api_sites)
    created_count = 0
    updated_count = 0
    unchanged_count = 0
    errors = []

    for item in api_sites:
        siteid = str(item.get('siteid') or '').strip()
        if not siteid:
            continue
        try:
            api_name = get_api_site_name(item)
            netbox_site = find_netbox_site_for_api(siteid, api_name, cf_name)
            if not netbox_site:
                clean_sitename = get_api_sitename2(item)
                desired_name = get_expected_netbox_site_name(siteid, clean_sitename)
                name = desired_name
                slug = slugify(desired_name) or slugify(siteid)
                if Site.objects.filter(slug=slug).exists():
                    slug = slugify(f"{desired_name}-site")[:100]
                if Site.objects.filter(name=name).exists():
                    name = f"{desired_name} (new)"[:100]
                counter = 1
                while Site.objects.filter(name=name).exists():
                    name = f"{desired_name} {counter}"[:100]
                    counter += 1

                netbox_site = Site(
                    name=name,
                    slug=slug,
                    description=desired_name,
                    status='active' if str(item.get('status')).strip().lower() == 'operational' else 'planned',
                    custom_field_data={
                        cf_name: siteid,
                        'site_name': clean_sitename or siteid
                    }
                )
                netbox_site.save()
                sync_one_site(netbox_site, item, cf_name)
                created_count += 1
                logger_func(f"Created new site: {siteid} ({netbox_site.name})")
            else:
                updated, logs_list = sync_one_site(netbox_site, item, cf_name)
                device_changes = [log for log in logs_list if any(kw in log for kw in ["Device", "Creating", "changed", "created"])]
                if updated or device_changes:
                    updated_count += 1
                    logger_func(f"Synchronized site: {siteid} ({netbox_site.name})")
                else:
                    unchanged_count += 1
        except Exception as e:
            err = f"Site {siteid}: {str(e)}"
            logger_func(f"Error syncing {err}")
            errors.append(err)

    summary = {
        'total': total,
        'created': created_count,
        'updated': updated_count,
        'unchanged': unchanged_count,
        'errors': errors
    }
    logger_func(f"Demandsite sync completed: {created_count} created, {updated_count} updated, {unchanged_count} unchanged, {len(errors)} errors out of {total} sites.")
    return summary


class DemandsiteListView(PermissionRequiredMixin, View):
    permission_required = 'netbox_demandsite.view_demandsite'
    template_name = 'netbox_demandsite/demandsite_list.html'

    def _get_api_data(self):
        return fetch_demand_api_data()

    def get(self, request):
        api_sites, api_error, is_fallback = self._get_api_data()
        if api_error:
            if is_fallback:
                messages.warning(request, f"Notice: {api_error}")
            else:
                messages.error(request, f"Failed to fetch site data from external server: {api_error}")
            
        cf_name = get_site_id_cf_name()
        choices_map = build_cf_choices_map()
        
        # Build mapping of NetBox sites by Site ID custom field (case-insensitive)
        # Groups multiple sites under the same key if duplicates exist
        netbox_sites_map = {}
        all_nb_sites = list(Site.objects.select_related('region'))
        for site in all_nb_sites:
            mapped = False
            if site.custom_field_data:
                site_id_val = site.custom_field_data.get(cf_name)
                if not site_id_val:
                    for k, v in site.custom_field_data.items():
                        if 'site' in k.lower() and 'id' in k.lower() and v:
                            site_id_val = v
                            break
                if site_id_val:
                    key = str(site_id_val).strip().upper()
                    if key not in netbox_sites_map:
                        netbox_sites_map[key] = []
                    netbox_sites_map[key].append(site)
                    mapped = True
            if not mapped and site.name:
                prefix = site.name.split('_')[0].split(' ')[0].strip().upper()
                if prefix:
                    if prefix not in netbox_sites_map:
                        netbox_sites_map[prefix] = []
                    netbox_sites_map[prefix].append(site)
                    
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
        site_name_cf_key = None
        
        for cf in cf_fields:
            cf_name_lower = cf.name.lower()
            if 'district' in cf_name_lower:
                district_key = cf.name
            if 'site' in cf_name_lower and 'name' in cf_name_lower:
                site_name_cf_key = cf.name
            if all(kw in cf_name_lower for kw in ['local', 'level', 'name']) or all(kw in cf_name_lower for kw in ['palika']):
                if not ('type' in cf_name_lower or ('level' in cf_name_lower and not 'name' in cf_name_lower)):
                    local_level_name_key = cf.name
            if all(kw in cf_name_lower for kw in ['local', 'level']) or all(kw in cf_name_lower for kw in ['palika', 'type']):
                if 'type' in cf_name_lower or ('level' in cf_name_lower and not 'name' in cf_name_lower):
                    local_level_type_key = cf.name
            if 'ward' in cf_name_lower:
                ward_key = cf.name
                
        if local_level_type_key == local_level_name_key:
            local_level_type_key = None
        if not site_name_cf_key:
            site_name_cf_key = 'site_name'

        # Pre-fetch BTS devices for all NetBox sites in one efficient query
        from collections import defaultdict
        site_devices_map = defaultdict(list)
        BTS_ROLES = {'WSD/BTS/2G', 'WSD/BTS/3G', 'WSD/BTS/4G'}
        SUFFIX_TECH = {'_G': '2g', '_U': '3g', '_L': '4g'}
        try:
            bts_device_qs = Device.objects.filter(role__name__in=BTS_ROLES).values(
                'site_id', 'name', 'status', 'primary_ip4__address'
            )
            for d in bts_device_qs:
                ip_addr = d.get('primary_ip4__address')
                d['ip'] = str(ip_addr).split('/')[0] if ip_addr else ''
                site_devices_map[d['site_id']].append(d)
        except Exception:
            try:
                bts_device_qs = Device.objects.filter(device_role__name__in=BTS_ROLES).values(
                    'site_id', 'name', 'status', 'primary_ip4__address'
                )
                for d in bts_device_qs:
                    ip_addr = d.get('primary_ip4__address')
                    d['ip'] = str(ip_addr).split('/')[0] if ip_addr else ''
                    site_devices_map[d['site_id']].append(d)
            except Exception:
                try:
                    bts_device_qs = Device.objects.filter(role__name__in=BTS_ROLES).values('site_id', 'name', 'status')
                    for d in bts_device_qs:
                        d['ip'] = ''
                        site_devices_map[d['site_id']].append(d)
                except Exception:
                    bts_device_qs = Device.objects.filter(device_role__name__in=BTS_ROLES).values('site_id', 'name', 'status')
                    for d in bts_device_qs:
                        d['ip'] = ''
                        site_devices_map[d['site_id']].append(d)
            
        correlated_sites = []
        total_mismatch = 0
        total_matched = 0
        total_not_in_netbox = 0
        total_synced = 0
        
        for item in api_sites:
            siteid = str(item.get('siteid', '')).strip()
            api_sitename = get_api_sitename2(item)
            
            site_list = netbox_sites_map.get(siteid.upper(), [])
            matched_site = find_best_site_match(site_list, api_sitename)
            expected_site_name = get_expected_netbox_site_name(siteid, api_sitename)
            
            # Format API technologies
            api_tech_flags = parse_api_technologies(item)
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
            if not tech_list:
                if api_tech_flags.get('2g'):
                    tech_list.append('2G')
                if api_tech_flags.get('3g'):
                    tech_list.append('3G')
                if api_tech_flags.get('4g'):
                    tech_list.append('4G')
            api_techs = sorted(list(set(tech_list)))
            
            api_data = {
                'siteid': siteid,
                'sitename': api_sitename or '—',
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
            siteid_diff = False
            tech_diff = False
            
            if not matched_site:
                has_mismatch = True
                needs_sync = True
            else:
                nb_cf_site_id = resolve_cf_display(cf_name, matched_site.custom_field_data.get(cf_name), choices_map) if matched_site.custom_field_data else '—'
                nb_site_name = matched_site.custom_field_data.get(site_name_cf_key) if matched_site.custom_field_data else None
                if not nb_site_name and matched_site.custom_field_data:
                    nb_site_name = matched_site.custom_field_data.get('site_name')
                
                devs_for_site = site_devices_map.get(matched_site.id, [])
                nb_devices_list = [{'name': d['name'], 'status': d['status'], 'ip': d.get('ip', '')} for d in devs_for_site]
                
                nb_data = {
                    'site_id': nb_cf_site_id,
                    'name': nb_site_name or '—',
                    'region': matched_site.region.name if matched_site.region else '—',
                    'district': resolve_cf_display(district_key, matched_site.custom_field_data.get(district_key), choices_map) if district_key and matched_site.custom_field_data else '—',
                    'local_level_name': resolve_cf_display(local_level_name_key, matched_site.custom_field_data.get(local_level_name_key), choices_map) if local_level_name_key and matched_site.custom_field_data else '—',
                    'local_level': resolve_cf_display(local_level_type_key, matched_site.custom_field_data.get(local_level_type_key), choices_map) if local_level_type_key and matched_site.custom_field_data else '—',
                    'ward': resolve_cf_display(ward_key, matched_site.custom_field_data.get(ward_key), choices_map) if ward_key and matched_site.custom_field_data else '—',
                    'latitude': matched_site.latitude if matched_site.latitude is not None else '—',
                    'longitude': matched_site.longitude if matched_site.longitude is not None else '—',
                    'status': matched_site.get_status_display() if hasattr(matched_site, 'get_status_display') else str(matched_site.status),
                    'devices': nb_devices_list,
                }
                
                # 1. Site Name in NetBox: expected to match SiteID_sitename2
                siteid_diff = not is_site_name_match(matched_site.name, expected_site_name)
                
                # 2. Sitename2 vs NetBox site_name custom field
                nb_name_str = str(nb_data['name']).strip() if nb_data['name'] != '—' else ''
                if not is_placeholder(api_sitename):
                    name_diff = (nb_name_str.upper() != api_sitename.upper())
                elif nb_name_str:
                    # Present in NetBox and not in Demand: show in mismatch list
                    name_diff = True
                else:
                    name_diff = False
                    
                # 3. Status: Operational -> active; otherwise -> planned
                api_status_str = str(item.get('status', '')).strip().lower()
                if not is_placeholder(api_status_str):
                    if api_status_str == 'operational':
                        expected_nb_status = 'active'
                    elif api_status_str == 'discontinued':
                        expected_nb_status = 'decommissioning'
                    else:
                        expected_nb_status = 'planned'
                    status_diff = (matched_site.status.lower() != expected_nb_status)
                else:
                    status_diff = False
                
                # 4. Coordinates
                lat_diff = False
                lon_diff = False
                api_lat = item.get('latitude')
                api_lon = item.get('longitude')
                if not is_placeholder(api_lat):
                    try:
                        if matched_site.latitude is None or abs(float(matched_site.latitude) - float(api_lat)) > 0.0001:
                            lat_diff = True
                    except Exception:
                        pass
                elif matched_site.latitude is not None:
                    # Present in NetBox and not in Demand: show in mismatch list
                    lat_diff = True
                    
                if not is_placeholder(api_lon):
                    try:
                        if matched_site.longitude is None or abs(float(matched_site.longitude) - float(api_lon)) > 0.0001:
                            lon_diff = True
                    except Exception:
                        pass
                elif matched_site.longitude is not None:
                    # Present in NetBox and not in Demand: show in mismatch list
                    lon_diff = True
                        
                # 5. Province / Region
                region_diff = False
                api_province = clean_province_name(item.get('province'))
                nb_region = str(nb_data['region']).strip()
                if not is_placeholder(api_province):
                    if nb_region and nb_region != '—':
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
                    else:
                        region_diff = True
                elif nb_region and nb_region != '—':
                    # Present in NetBox and not in Demand: show in mismatch list
                    region_diff = True
                        
                # 6. District
                district_diff = False
                api_district = item.get('district')
                nb_dist = str(nb_data['district']).strip() if nb_data['district'] != '—' else ''
                if not is_placeholder(api_district):
                    if str(api_district).strip().lower() != nb_dist.lower():
                        district_diff = True
                elif nb_dist:
                    # Present in NetBox and not in Demand: show in mismatch list
                    district_diff = True
                        
                # 7. Palika / Local Level Name (Janakpur rule & suffixes stripped)
                palika_diff = False
                api_palika = item.get('palika')
                nb_palika = str(nb_data['local_level_name']).strip() if nb_data['local_level_name'] != '—' else ''
                if not is_placeholder(api_palika):
                    api_palika_clean = clean_palika_name(api_palika)
                    nb_palika_clean = clean_palika_name(nb_palika)
                    if api_palika_clean.lower() != nb_palika_clean.lower():
                        api_first = api_palika_clean.split()[0].lower() if api_palika_clean else ''
                        nb_first = nb_palika_clean.split()[0].lower() if nb_palika_clean else ''
                        if not (api_first and api_first == nb_first):
                            palika_diff = True
                elif nb_palika:
                    # Present in NetBox and not in Demand: show in mismatch list
                    palika_diff = True
                            
                # 8. Palika Type / Local Level Type
                local_level_diff = False
                api_palika_type = item.get('palika_type')
                nb_local_level = str(nb_data['local_level']).strip() if nb_data['local_level'] != '—' else ''
                if not is_placeholder(api_palika_type):
                    mapping = {
                        'RuralMunicipality': 'Rural Municipality',
                        'Municipality': 'Municipality',
                        'Metropolitan': 'Metropolitan',
                        'SubMetropolitan': 'Sub-Metropolitan',
                        'Sub-Metropolitan': 'Sub-Metropolitan',
                    }
                    norm_api_type = mapping.get(api_palika_type, api_palika_type)
                    if nb_local_level.lower() != norm_api_type.strip().lower():
                        local_level_diff = True
                elif nb_local_level:
                    # Present in NetBox and not in Demand: show in mismatch list
                    local_level_diff = True
                        
                # 9. Ward
                ward_diff = False
                api_ward = item.get('wardno')
                nb_ward = str(nb_data['ward']).strip() if nb_data['ward'] != '—' else ''
                if not is_placeholder(api_ward):
                    if nb_ward != str(api_ward).strip():
                        ward_diff = True
                elif nb_ward:
                    # Present in NetBox and not in Demand: show in mismatch list
                    ward_diff = True
                        
                # 10. Technology / Devices
                tech_diff = False
                for suffix, tech_key in SUFFIX_TECH.items():
                    tech_present = api_tech_flags.get(tech_key, False)
                    found_dev = None
                    prefix_check = f"{siteid}_".upper()
                    suffix_check = suffix.upper()
                    for d in devs_for_site:
                        d_name_upper = d['name'].upper()
                        if d_name_upper.startswith(prefix_check) and d_name_upper.endswith(suffix_check):
                            found_dev = d
                            break
                    if tech_present:
                        if found_dev is None or found_dev['status'] != 'active':
                            tech_diff = True
                            break
                    else:
                        # Present in NetBox and not in Demand: show in mismatch list
                        if found_dev is not None and found_dev['status'] == 'active':
                            tech_diff = True
                            break
                            
                has_mismatch = (
                    siteid_diff or name_diff or status_diff or
                    region_diff or district_diff or palika_diff or
                    local_level_diff or ward_diff or lat_diff or lon_diff or tech_diff
                )
                needs_sync = has_mismatch

            if matched_site:
                total_matched += 1
                if has_mismatch:
                    total_mismatch += 1
                else:
                    total_synced += 1
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
                    
            mismatches = []
            if siteid_diff or name_diff:
                mismatches.append('Name')
            if region_diff:
                mismatches.append('Province')
            if district_diff:
                mismatches.append('District')
            if palika_diff:
                mismatches.append('Palika')
            if local_level_diff:
                mismatches.append('Type')
            if ward_diff:
                mismatches.append('Ward')
            if lat_diff or lon_diff:
                mismatches.append('Coordinates')
            if status_diff:
                mismatches.append('Status')
            if tech_diff:
                mismatches.append('Technology')

            diffs = {
                'siteid': siteid_diff,
                'name': name_diff,
                'province': region_diff,
                'district': district_diff,
                'palika': palika_diff,
                'palika_type': local_level_diff,
                'ward': ward_diff,
                'lat': lat_diff,
                'lon': lon_diff,
                'status': status_diff,
                'tech': tech_diff,
            }
            
            correlated_sites.append({
                'api_data': api_data,
                'nb_data': nb_data,
                'netbox_site': matched_site,
                'expected_name': expected_site_name,
                'has_mismatch': has_mismatch,
                'needs_sync': needs_sync,
                'diffs': diffs,
                'mismatches': mismatches,
                'mismatch_summary': ', '.join(mismatches),
                'siteid_diff': siteid_diff,
                'name_diff': name_diff,
                'region_diff': region_diff,
                'district_diff': district_diff,
                'palika_diff': palika_diff,
                'local_level_diff': local_level_diff,
                'ward_diff': ward_diff,
                'lat_diff': lat_diff,
                'lon_diff': lon_diff,
                'status_diff': status_diff,
                'tech_diff': tech_diff,
                '_api_techs': api_tech_flags,
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
                elif filter_type == 'synced' and item['netbox_site'] is not None and not item['has_mismatch']:
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

        sync_logs = request.session.pop('last_sync_logs', None)
        from django.core.cache import cache
        auto_sync_log = cache.get('demandsite_auto_sync_log')

        context = {
            'correlated_sites': paginated_sites,
            'paginator': paginator,
            'total_api_sites': total_api_sites,
            'total_netbox': total_netbox,
            'total_matched': total_matched,
            'total_not_in_netbox': total_not_in_netbox,
            'total_mismatch': total_mismatch,
            'total_synced': total_synced,
            'total_filtered_sites': total_filtered_sites,
            'cf_name': cf_name,
            'q': request.GET.get('q', ''),
            'filter': filter_type,
            'sync_logs': sync_logs,
            'auto_sync_log': auto_sync_log,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        sync_siteid = request.POST.get('sync_siteid') or request.POST.get('siteid')
        action = request.POST.get('action')
        if sync_siteid:
            action = 'sync_single'

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

        api_sites, api_error, is_fallback = self._get_api_data()
        if api_error and not is_fallback:
            messages.error(request, f"Sync failed: {api_error}")
            return redirect(redirect_url)
        elif api_error and is_fallback:
            messages.warning(request, f"Notice: {api_error} Syncing from cached snapshot.")
            
        from django.utils import timezone
        now_str = timezone.now().strftime('%Y-%m-%d %H:%M:%S')

        if action == 'sync_single':
            siteid = str(sync_siteid or '').strip()
            if not siteid:
                messages.error(request, "Failed to sync: Site ID not provided.")
                return redirect(redirect_url)
                
            api_site = next((x for x in api_sites if str(x.get('siteid')).strip().upper() == siteid.upper()), None)
            if not api_site:
                messages.error(request, f"Failed to sync site {siteid}. Site not found in API data.")
                return redirect(redirect_url)
                
            api_name = get_api_site_name(api_site)
            netbox_site = find_netbox_site_for_api(siteid, api_name, cf_name)
            
            try:
                if not netbox_site:
                    # Create new site
                    clean_sitename = get_api_sitename2(api_site)
                    desired_name = get_expected_netbox_site_name(siteid, clean_sitename)
                    
                    name = desired_name
                    slug = slugify(desired_name) or slugify(siteid)
                    
                    if Site.objects.filter(slug=slug).exists():
                        slug = slugify(f"{desired_name}-site")[:100]
                    if Site.objects.filter(name=name).exists():
                        name = f"{desired_name} (new)"[:100]
                    
                    counter = 1
                    while Site.objects.filter(name=name).exists():
                        name = f"{desired_name} {counter}"[:100]
                        counter += 1
                        
                    netbox_site = Site(
                        name=name,
                        slug=slug,
                        description=desired_name,
                        status='active' if str(api_site.get('status')).strip().lower() == 'operational' else 'planned',
                        custom_field_data={
                            cf_name: siteid,
                            'site_name': clean_sitename or siteid
                        }
                    )
                    netbox_site.save()
                    _, site_logs = sync_one_site(netbox_site, api_site, cf_name)
                    created_logs = [f"Created NetBox Site: {netbox_site.name} (Status: {netbox_site.status})"] + site_logs
                    request.session['last_sync_logs'] = {
                        'siteid': f"{siteid} ({netbox_site.name})",
                        'logs': created_logs,
                        'time': now_str
                    }
                    messages.success(request, f"Successfully created and synchronized site {siteid} ({netbox_site.name}) in NetBox.")
                else:
                    updated, logs_list = sync_one_site(netbox_site, api_site, cf_name)
                    request.session['last_sync_logs'] = {
                        'siteid': f"{siteid} ({netbox_site.name})",
                        'logs': logs_list if logs_list else ["All fields already match external Demand data."],
                        'time': now_str
                    }
                    device_changes = [log for log in logs_list if any(kw in log for kw in ["Device", "Creating", "changed", "created"])]
                    if updated or device_changes:
                        messages.success(request, f"Successfully synchronized fields for {siteid} ({netbox_site.name}) to NetBox.")
                    else:
                        messages.info(request, f"Site {siteid} ({netbox_site.name}) is synchronized. Existing NetBox values not present in Demand were preserved.")
            except Exception as e:
                import traceback
                logger.error(f"Sync error for site {siteid}: {traceback.format_exc()}")
                messages.error(request, f"Sync error for site {siteid}: {str(e)}")
                
        elif action == 'sync_selected':
            siteids = request.POST.getlist('selected_siteids')
            if not siteids:
                messages.error(request, "Failed to sync: No sites selected.")
                return redirect(redirect_url)
                
            success_count = 0
            errors = []
            all_selected_logs = []

            for siteid in siteids:
                if not siteid:
                    continue
                siteid_str = str(siteid).strip()
                api_site = next((x for x in api_sites if str(x.get('siteid')).strip().upper() == siteid_str.upper()), None)
                if not api_site:
                    errors.append(f"Site {siteid_str} not found in API.")
                    continue
                    
                api_name = get_api_site_name(api_site)
                netbox_site = find_netbox_site_for_api(siteid_str, api_name, cf_name)
                
                try:
                    if not netbox_site:
                        clean_sitename = get_api_sitename2(api_site)
                        desired_name = get_expected_netbox_site_name(siteid_str, clean_sitename)
                        
                        name = desired_name
                        slug = slugify(desired_name) or slugify(siteid_str)
                        
                        if Site.objects.filter(slug=slug).exists():
                            slug = slugify(f"{desired_name}-site")[:100]
                        if Site.objects.filter(name=name).exists():
                            name = f"{desired_name} (new)"[:100]
                        
                        counter = 1
                        while Site.objects.filter(name=name).exists():
                            name = f"{desired_name} {counter}"[:100]
                            counter += 1
                            
                        netbox_site = Site(
                            name=name,
                            slug=slug,
                            description=desired_name,
                            status='active' if str(api_site.get('status')).strip().lower() == 'operational' else 'planned',
                            custom_field_data={
                                cf_name: siteid_str,
                                'site_name': clean_sitename or siteid_str
                            }
                        )
                        netbox_site.save()
                        _, site_logs = sync_one_site(netbox_site, api_site, cf_name)
                        all_selected_logs.append(f"Created {siteid_str} ({netbox_site.name}): " + ("; ".join(site_logs[:3]) if site_logs else "initial sync"))
                    else:
                        updated, site_logs = sync_one_site(netbox_site, api_site, cf_name)
                        if site_logs:
                            all_selected_logs.append(f"Updated {siteid_str} ({netbox_site.name}): " + "; ".join(site_logs[:4]))
                        else:
                            all_selected_logs.append(f"Checked {siteid_str} ({netbox_site.name}): Values match (no changes).")
                    success_count += 1
                except Exception as e:
                    import traceback
                    logger.error(f"Sync error for site {siteid_str}: {traceback.format_exc()}")
                    errors.append(f"Site {siteid_str}: {str(e)}")
                    all_selected_logs.append(f"Error on {siteid_str}: {str(e)}")
                    
            if success_count > 0:
                request.session['last_sync_logs'] = {
                    'siteid': f"{success_count} Selected Sites",
                    'logs': all_selected_logs,
                    'time': now_str
                }
                messages.success(request, f"Successfully synchronized {success_count} sites.")
            if errors:
                messages.error(request, f"Failed to sync some sites: {', '.join(errors[:5])}")

        elif action == 'sync_all':
            result = auto_sync_missing_demand_sites()
            request.session['last_sync_logs'] = {
                'siteid': "24-Hour Auto-Sync (Missing Sites)",
                'logs': result.get('logs', []),
                'time': now_str
            }
            messages.success(
                request,
                f"Auto-Sync executed: {result['created']} missing sites imported into NetBox, {result['skipped']} existing sites preserved out of {result['total']} Demand sites."
            )
            if result.get('errors'):
                messages.warning(request, f"{len(result['errors'])} errors occurred: {', '.join(result['errors'][:5])}")
                
        return redirect(redirect_url)


class DemandsiteDetailView(PermissionRequiredMixin, View):
    permission_required = 'netbox_demandsite.view_demandsite'
    """
    Detail page redirects to list.
    """
    def get(self, request, siteid):
        return redirect('plugins:netbox_demandsite:demandsite_list')

    def post(self, request, siteid):
        return redirect('plugins:netbox_demandsite:demandsite_list')


class DemandsiteServerView(PermissionRequiredMixin, View):
    permission_required = 'netbox_demandsite.view_demandsite'
    """
    Server overview dashboard showing API connection status,
    total sites, and technology counts (2G, 3G, 4G).
    """
    template_name = 'netbox_demandsite/demandsite_server.html'

    def get(self, request):
        list_view = DemandsiteListView()
        api_sites, api_error, is_fallback = list_view._get_api_data()

        from django.conf import settings
        plugin_config = settings.PLUGINS_CONFIG.get('netbox_demandsite', {})
        api_url = plugin_config.get('api_url', 'https://demandsite.ntc.net.np/api/share/site-dimension')
        api_token = plugin_config.get('api_token', 'ds_share_7b4a2f8c1e9d3056bf47e382d61a9c8f')

        masked_token = f"{api_token[:14]}... [configured]" if (api_token and len(api_token) > 14) else "[configured]"

        total_sites = len(api_sites)
        count_2g = 0
        count_3g = 0
        count_4g = 0
        provinces = set()
        districts = set()
        palikas = set()

        for site in api_sites:
            techs = parse_api_technologies(site)
            if techs.get('2g'):
                count_2g += 1
            if techs.get('3g'):
                count_3g += 1
            if techs.get('4g'):
                count_4g += 1
            if site.get('province') and site.get('province') != '—':
                provinces.add(str(site.get('province')).strip())
            if site.get('district') and site.get('district') != '—':
                districts.add(str(site.get('district')).strip())
            if site.get('palika') and site.get('palika') != '—':
                palikas.add(str(site.get('palika')).strip())

        is_connected = (api_error is None) or is_fallback
        if not api_error:
            status_label = "Connected"
            status_color = "success"
        elif is_fallback:
            status_label = "Connected (Cached)"
            status_color = "warning"
        else:
            status_label = "Offline"
            status_color = "danger"

        from django.core.cache import cache
        auto_sync_log = cache.get('demandsite_auto_sync_log')

        context = {
            'api_url': api_url,
            'masked_token': masked_token,
            'api_error': api_error,
            'is_fallback': is_fallback,
            'is_connected': is_connected,
            'status_label': status_label,
            'status_color': status_color,
            'total_sites': total_sites,
            'count_2g': count_2g,
            'count_3g': count_3g,
            'count_4g': count_4g,
            'total_provinces': len(provinces),
            'total_districts': len(districts),
            'total_palikas': len(palikas),
            'auto_sync_log': auto_sync_log,
        }
        return render(request, self.template_name, context)


class DemandsiteRawView(PermissionRequiredMixin, View):
    permission_required = 'netbox_demandsite.view_demandsite'
    template_name = 'netbox_demandsite/demandsite_raw.html'
    """
    Displays the raw Demand Site API data in a full table view,
    with search filtering, pagination, metric counters, and optional ?format=json.
    """
    def get(self, request):
        list_view = DemandsiteListView()
        api_sites, api_error, is_fallback = list_view._get_api_data()
        
        # If user explicitly requested JSON
        if request.GET.get('format') == 'json':
            if api_error and not api_sites:
                return JsonResponse({'error': api_error}, status=502)
            return JsonResponse(api_sites, safe=False, json_dumps_params={'indent': 2})
            
        if api_error:
            if is_fallback:
                messages.warning(request, f"Notice: {api_error} (showing cached snapshot)")
            else:
                messages.error(request, f"Failed to fetch site data: {api_error}")
                
        total_raw_sites = len(api_sites)
        count_operational = 0
        count_planned = 0
        count_with_coords = 0
        
        for s in api_sites:
            status_val = str(s.get('status') or '').strip().lower()
            if status_val == 'operational':
                count_operational += 1
            elif status_val == 'planned':
                count_planned += 1
            lat = s.get('latitude')
            lon = s.get('longitude')
            if not is_placeholder(lat) and not is_placeholder(lon):
                count_with_coords += 1
                
        # Search filter
        q = request.GET.get('q', '').strip().upper()
        filtered_sites = []
        for idx, item in enumerate(api_sites, 1):
            siteid = str(item.get('siteid') or '').strip().upper()
            sname1 = str(item.get('sitename1') or '').strip().upper()
            sname2 = str(item.get('sitename2') or '').strip().upper()
            prov = str(item.get('province') or '').strip().upper()
            dist = str(item.get('district') or '').strip().upper()
            pal = str(item.get('palika') or '').strip().upper()
            status = str(item.get('status') or '').strip().upper()
            
            if q:
                if (q not in siteid and 
                    q not in sname1 and 
                    q not in sname2 and 
                    q not in prov and 
                    q not in dist and 
                    q not in pal and 
                    q not in status):
                    continue
            
            # Format technologies
            tech_flags = parse_api_technologies(item)
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
            if not tech_list:
                if tech_flags.get('2g'): tech_list.append('2G')
                if tech_flags.get('3g'): tech_list.append('3G')
                if tech_flags.get('4g'): tech_list.append('4G')
            
            filtered_sites.append({
                'index': idx,
                'siteid': item.get('siteid') or '—',
                'sitename1': item.get('sitename1') or '—',
                'sitename2': item.get('sitename2') or '—',
                'province': item.get('province') or '—',
                'district': item.get('district') or '—',
                'palika': item.get('palika') or '—',
                'palika_type': item.get('palika_type') or '—',
                'wardno': item.get('wardno') if item.get('wardno') is not None else '—',
                'latitude': item.get('latitude') or '—',
                'longitude': item.get('longitude') or '—',
                'status': item.get('status') or '—',
                'technologies': sorted(list(set(tech_list))),
            })
            
        from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
        paginator = Paginator(filtered_sites, 50)
        page_num = request.GET.get('page', 1)
        try:
            paginated_sites = paginator.page(page_num)
        except PageNotAnInteger:
            paginated_sites = paginator.page(1)
        except EmptyPage:
            paginated_sites = paginator.page(paginator.num_pages)
            
        context = {
            'sites': paginated_sites,
            'paginator': paginator,
            'total_raw_sites': total_raw_sites,
            'count_operational': count_operational,
            'count_planned': count_planned,
            'count_with_coords': count_with_coords,
            'total_filtered': len(filtered_sites),
            'q': request.GET.get('q', ''),
        }
        return render(request, self.template_name, context)

