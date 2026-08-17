# -*- coding: utf-8 -*-
"""Deprecated compatibility shim for the retired MapControPre entry point.

The historical implementation delegated online inference to
``map_control.cluster.online_cluster.RealtimeProcessor``.  That path is no
longer part of the formal WFGD slurry-control architecture.

The active online path is now:

    ProcessForMapConsole.insert_Mod()
        -> OnlineConditionPolicyPipeline.process()
        -> FastChangeHistoryManager
        -> OnlineConditionClassifier
        -> SlurryPolicyOnlineBridge / OnlineSlurryPolicy

This module is intentionally kept as a very small compatibility shell so that
an old import does not fail during the transition period.  It must not perform
online inference and must not re-introduce ``online_cluster``.

After all external/deployment references to ``MapControPre`` have been
confirmed removed, this file and the compatibility getter in
``Process4MapControl.py`` can be deleted completely.
"""


class MapControPre:
    """Retired legacy entry point.

    New code must use ``ProcessForMapConsole`` and its integrated online
    condition + slurry-policy pipeline instead.
    """

    def __init__(self, *args, **kwargs):
        # Deliberately do not initialize database connections, legacy models,
        # or any online-cluster processor.
        self.retired = True

    def map_console(self, row):
        """Reject legacy online inference calls explicitly.

        Keeping a hard failure here is safer than silently producing a result
        from an obsolete algorithm path.
        """
        raise RuntimeError(
            "MapControPre.map_console() has been retired. "
            "Use ProcessForMapConsole.insert_Mod() / "
            "OnlineConditionPolicyPipeline.process() instead."
        )
