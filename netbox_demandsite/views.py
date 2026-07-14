import logging
import requests
from decimal import Decimal
from django.shortcuts import render, redirect
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from dcim.models import Site
from extras.models import CustomField
from django.contrib.contenttypes.models import ContentType

logger = logging.getLogger('netbox.plugins.netbox_demandsite')

class DemandsiteListView(LoginRequiredMixin, View):
    template_name = 'netbox_demandsite/demandsite_list.html'
    
    def _get_site_id_cf_name(self):
        """
        Dynamically finds the custom field name that stores the Site ID.
        Looks for a custom field containing 'site' and 'id' (case-insensitive).
        """
        site_ct = ContentType.objects.get_for_model(Site)
        cf_fields = CustomField.objects.filter(content_types=site_ct)
        for cf in cf_fields:
            if 'site' in cf.name.lower() and 'id' in cf.name.lower():
                return cf.name
        
        # Fallback inspection of keys in existing site instances
        for site in Site.objects.all()[:20]:
            if site.custom_field_data:
                for key in site.custom_field_data.keys():
                    if 'site' in key.lower() and 'id' in key.lower():
                        return key
        return 'site_id'  # Default fallback

    def _get_api_data(self):
        url = "https://demandsite.ntc.net.np/api/share/site-dimension"
        headers = {
            "Authorization": "Bearer ds_share_7b4a2f8c1e9d3056bf47e382d61a9c8f"
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
            
        cf_name = self._get_site_id_cf_name()
        
        # Build mapping of NetBox sites by Site ID custom field (case-insensitive)
        netbox_sites_map = {}
        for site in Site.objects.all():
            if site.custom_field_data:
                site_id_val = site.custom_field_data.get(cf_name)
                if site_id_val:
                    netbox_sites_map[str(site_id_val).strip().upper()] = site
                    
        correlated_sites = []
        total_matched = 0
        total_planned = 0
        total_operational = 0
        
        # Search query filter from search box
        q = request.GET.get('q', '').strip().upper()
        
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
                
                api_lat = item.get('latitude')
                api_lon = item.get('longitude')
                
                # Check status translation
                if status == 'Operational' and matched_site.status != 'active':
                    status_diff = True
                elif status == 'Planned' and matched_site.status != 'planned':
                    status_diff = True
                    
                # Compare latitude
                if api_lat:
                    try:
                        if not matched_site.latitude or abs(float(matched_site.latitude) - float(api_lat)) > 0.00001:
                            lat_diff = True
                    except (ValueError, TypeError):
                        pass
                    
                # Compare longitude
                if api_lon:
                    try:
                        if not matched_site.longitude or abs(float(matched_site.longitude) - float(api_lon)) > 0.00001:
                            lon_diff = True
                    except (ValueError, TypeError):
                        pass
                    
                if lat_diff or lon_diff or status_diff:
                    sync_status = "Out of Sync"
                    sync_color = "warning"
                    needs_sync = True
                else:
                    sync_status = "Synchronized"
                    sync_color = "success"
                    
            # Apply basic filtering
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
                'api_data': item,
                'netbox_site': matched_site,
                'sync_status': sync_status,
                'sync_color': sync_color,
                'needs_sync': needs_sync
            })
            
        context = {
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
        cf_name = self._get_site_id_cf_name()
        
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
                if self._sync_one(netbox_site, api_site):
                    messages.success(request, f"Successfully synchronized site {siteid} ({netbox_site.name})")
                else:
                    messages.info(request, f"Site {siteid} ({netbox_site.name}) is already synchronized.")
            else:
                messages.error(request, f"Failed to sync site {siteid}. Make sure it exists in both systems.")
                
        elif action == 'sync_all':
            for api_site in api_sites:
                siteid = api_site.get('siteid')
                netbox_site = netbox_sites_map.get(str(siteid).strip().upper())
                if netbox_site:
                    if self._sync_one(netbox_site, api_site):
                        sync_count += 1
            if sync_count > 0:
                messages.success(request, f"Successfully synchronized {sync_count} sites.")
            else:
                messages.info(request, "All matched sites are already synchronized.")
                
        return redirect('plugins:netbox_demandsite:demandsite_list')

    def _sync_one(self, netbox_site, api_site):
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
            
        if updated:
            netbox_site.save()
            return True
        return False
