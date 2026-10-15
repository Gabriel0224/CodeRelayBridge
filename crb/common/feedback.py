"""Static issue-form metadata; never collect diagnostics or contact GitHub."""
from desktop_client.edition import edition
from desktop_client.relay_updates import repository

def issue_form_url():
    return 'https://github.com/' + repository('server', edition()) + '/issues/new?template=bug_report.yml'
