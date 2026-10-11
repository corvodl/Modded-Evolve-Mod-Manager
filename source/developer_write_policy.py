"""Session-only authorization for Evolve Developer memory writes.

This file does not switch firewall rules, persist consent, or permit writes to
any process other than the existing Evolve.exe-scoped memory interface.
"""
from __future__ import annotations


def require_developer_write(enabled, online_opted_in, online_access_enabled,
                            game_root_provider, offline_rule_check):
    """Raise unless this session explicitly authorized writes.

    Default policy is read-only. Private/offline writes require outbound game
    isolation; online writes need a second explicit, session-only opt-in and
    the separately enabled online-network switch.
    """
    if enabled is not True:
        raise PermissionError(
            'Memory modification is disabled. Enable Memory Writing in the '
            'unlocked Developer page before editing game memory.')
    if (online_opted_in is True and online_access_enabled is True):
        return 'online-consented'
    if game_root_provider is None:
        raise PermissionError('A verified Evolve game folder is required.')
    if offline_rule_check(game_root_provider()) is not True:
        raise PermissionError(
            'Memory writes require an active Evolve outbound firewall block '
            'or an explicit online-writing risk acknowledgment together with '
            'online network access enabled.')
    return 'offline-isolated'
