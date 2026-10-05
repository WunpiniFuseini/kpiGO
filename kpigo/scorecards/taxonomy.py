"""Where each metric renders in the active taxonomy."""

from __future__ import annotations

from dataclasses import dataclass, field

from kpigo.scorecards.models import ScorecardNode, ScorecardTemplate


@dataclass(frozen=True)
class NodeInfo:
    node_id: str
    label: str
    level_no: int
    parent_id: str | None
    sort_order: int


@dataclass
class Placer:
    """Resolves a metric to its node: the profile's own placement, else the default."""

    template_id: str | None = None
    levels: list[str] = field(default_factory=list)
    nodes: dict[str, NodeInfo] = field(default_factory=dict)
    by_profile: dict[tuple[str, str | None], tuple[str, int]] = field(default_factory=dict)

    @classmethod
    def active(cls, org_id: str) -> Placer:
        template = ScorecardTemplate.objects.filter(org_id=org_id, status="active").first()
        if template is None:
            return cls()
        nodes = {
            str(n.node_id): NodeInfo(
                node_id=str(n.node_id),
                label=n.label,
                level_no=n.level_no,
                parent_id=str(n.parent_id) if n.parent_id else None,
                sort_order=n.sort_order,
            )
            for n in ScorecardNode.objects.filter(template=template)
        }
        return cls(
            template_id=str(template.template_id),
            levels=list(template.levels.order_by("level_no").values_list("label", flat=True)),
            nodes=nodes,
            by_profile={
                (p.metric_code, p.profile_code): (str(p.node_id), p.sort_order)
                for p in template.placements.all()
            },
        )

    def node_for(self, metric_code: str, profile_code: str | None) -> tuple[str, int] | None:
        return self.by_profile.get((metric_code, profile_code)) or self.by_profile.get(
            (metric_code, None)
        )

    def chain(self, node_id: str) -> list[NodeInfo]:
        """The node and its ancestors, top level first."""
        out: list[NodeInfo] = []
        current: str | None = node_id
        while current is not None and current in self.nodes:
            out.append(self.nodes[current])
            current = self.nodes[current].parent_id
        return list(reversed(out))

    def path(self, metric_code: str, profile_code: str | None) -> list[str]:
        found = self.node_for(metric_code, profile_code)
        return [n.label for n in self.chain(found[0])] if found else []
