"""
AppManager Hook and UI Slot Registry System.
Provides a decoupled, pluggable architecture for UI mount points and lifecycle hooks.
"""

from typing import Any, Callable, Dict, List, Optional

from markupsafe import Markup


class HookRegistry:
    """
    Central registry managing UI slots and lifecycle hooks for AppManager and its extensions.
    """

    def __init__(self) -> None:
        # slot_name -> list of dicts: {'callback': fn, 'priority': int, 'app_slug': str}
        self._slots: Dict[str, List[Dict[str, Any]]] = {}
        # hook_name -> list of dicts: {'callback': fn, 'priority': int, 'app_slug': str}
        self._hooks: Dict[str, List[Dict[str, Any]]] = {}

    def register_slot(
        self,
        slot_name: str,
        callback: Callable[..., Any],
        priority: int = 10,
        app_slug: Optional[str] = None,
    ) -> None:
        """
        Registers a UI slot renderer.
        Callback should return HTML string or Markup.
        Lower priority value runs earlier.
        """
        if slot_name not in self._slots:
            self._slots[slot_name] = []

        # Check if already registered to prevent duplicates
        for item in self._slots[slot_name]:
            if item["callback"] == callback and item.get("app_slug") == app_slug:
                item["priority"] = priority
                return

        self._slots[slot_name].append(
            {"callback": callback, "priority": priority, "app_slug": app_slug}
        )
        self._slots[slot_name].sort(key=lambda x: x["priority"])

    def unregister_slot(self, slot_name: str, app_slug: Optional[str] = None) -> None:
        """
        Unregisters all slot callbacks for a given app_slug or slot.
        """
        if slot_name in self._slots:
            if app_slug:
                self._slots[slot_name] = [
                    x for x in self._slots[slot_name] if x.get("app_slug") != app_slug
                ]
            else:
                del self._slots[slot_name]

    def render_slot(self, slot_name: str, *args: Any, **kwargs: Any) -> Markup:
        """
        Executes all registered callbacks for the slot in priority order
        and concatenates the rendered outputs safely as Jinja Markup.
        """
        entries = self._slots.get(slot_name, [])
        output_parts: List[str] = []

        for entry in entries:
            try:
                result = entry["callback"](*args, **kwargs)
                if result:
                    output_parts.append(str(result))
            except Exception as e:
                print(
                    f"[HOOK ERROR] Error rendering slot '{slot_name}' in app '{entry.get('app_slug')}': {e}"
                )

        return Markup("".join(output_parts))

    def register_hook(
        self,
        hook_name: str,
        callback: Callable[..., Any],
        priority: int = 10,
        app_slug: Optional[str] = None,
    ) -> None:
        """
        Registers a lifecycle hook callback.
        """
        if hook_name not in self._hooks:
            self._hooks[hook_name] = []

        for item in self._hooks[hook_name]:
            if item["callback"] == callback and item.get("app_slug") == app_slug:
                item["priority"] = priority
                return

        self._hooks[hook_name].append(
            {"callback": callback, "priority": priority, "app_slug": app_slug}
        )
        self._hooks[hook_name].sort(key=lambda x: x["priority"])

    def unregister_hook(self, hook_name: str, app_slug: Optional[str] = None) -> None:
        """
        Unregisters lifecycle hook callbacks.
        """
        if hook_name in self._hooks:
            if app_slug:
                self._hooks[hook_name] = [
                    x for x in self._hooks[hook_name] if x.get("app_slug") != app_slug
                ]
            else:
                del self._hooks[hook_name]

    def trigger_hook(self, hook_name: str, *args: Any, **kwargs: Any) -> List[Any]:
        """
        Triggers all registered callbacks for the lifecycle hook in priority order.
        Returns a list of non-None return values from callbacks.
        """
        entries = self._hooks.get(hook_name, [])
        results: List[Any] = []

        for entry in entries:
            try:
                res = entry["callback"](*args, **kwargs)
                if res is not None:
                    results.append(res)
            except Exception as e:
                print(
                    f"[HOOK ERROR] Error triggering hook '{hook_name}' in app '{entry.get('app_slug')}': {e}"
                )

        return results

    def get_registered_slots(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Returns snapshot of registered UI slots for inspection.
        """
        return {
            name: [
                {"app_slug": item.get("app_slug"), "priority": item["priority"]} for item in items
            ]
            for name, items in self._slots.items()
        }

    def get_registered_hooks(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Returns snapshot of registered lifecycle hooks for inspection.
        """
        return {
            name: [
                {"app_slug": item.get("app_slug"), "priority": item["priority"]} for item in items
            ]
            for name, items in self._hooks.items()
        }

    def clear(self) -> None:
        """
        Clears all registered slots and hooks.
        """
        self._slots.clear()
        self._hooks.clear()


# Global singleton instance
hooks = HookRegistry()


# Public helper functions
def register_slot(
    slot_name: str, callback: Callable[..., Any], priority: int = 10, app_slug: Optional[str] = None
) -> None:
    hooks.register_slot(slot_name, callback, priority, app_slug)


def render_slot(slot_name: str, *args: Any, **kwargs: Any) -> Markup:
    return hooks.render_slot(slot_name, *args, **kwargs)


def register_hook(
    hook_name: str, callback: Callable[..., Any], priority: int = 10, app_slug: Optional[str] = None
) -> None:
    hooks.register_hook(hook_name, callback, priority, app_slug)


def trigger_hook(hook_name: str, *args: Any, **kwargs: Any) -> List[Any]:
    return hooks.trigger_hook(hook_name, *args, **kwargs)
