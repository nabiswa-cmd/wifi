from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from .models import Payment, Subscription


class SubscriptionInline(admin.StackedInline):
    """
    Everything you need to answer 'why didn't this customer get online'
    without leaving the Payment page: the subscription it created, and a
    direct link to that subscription's MikroTik jobs (status + the exact
    error the agent reported, if any).
    """
    model = Subscription
    fk_name = 'payment'
    extra = 0
    readonly_fields = ('customer', 'package', 'status', 'mikrotik_username',
                        'activation_time', 'expiry_time', 'router_jobs_link')
    fields = readonly_fields
    can_delete = False

    def router_jobs_link(self, obj):
        if not obj.mikrotik_username:
            return ' '
        url = (reverse('admin:mikrotik_mikrotikjob_changelist')
               + f'?q={obj.mikrotik_username}')
        return format_html('<a href="{}">View MikroTik jobs for {}</a>', url, obj.mikrotik_username)
    router_jobs_link.short_description = 'Router status'



