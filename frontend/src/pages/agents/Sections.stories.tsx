import type { Meta, StoryObj } from "@storybook/react-vite";

import { distribution, pipeline, pipelineEmpty, pipelineNoStages, pipelineRms, pipelineStages, pipelineStagesNoMetrics, pipelineStagesNone, distributionEmpty, heatmap, heatmapEmpty, presetRestricted, trend, trendAverage, trendEmpty, visibilityOpen, visibilityRules } from "../../stories/agentFixtures";
import { PipelineStagesView, PipelineView } from "./Pipeline";
import { DistributionView, HeatmapView, TrendView } from "./Sections";
import { VisibilityNote, VisibilityView } from "./Visibility";

const meta: Meta = { title: "Pages/Agent Performance sections" };
export default meta;
type Story = StoryObj;

const card = (node: React.ReactNode): Story => ({ render: () => <div className="kg-card">{node}</div> });

/** Running total against where the targets expect it. */
export const TrendRunningTotal: Story = card(<TrendView trend={trend} title="Month to date" />);
/** An average: the daily mean against the target line. */
export const TrendAverage: Story = card(<TrendView trend={trendAverage} title="Queue trend" />);
/** Nothing reported yet this month. */
export const TrendEmpty: Story = card(<TrendView trend={trendEmpty} title="Month to date" />);
/** Branches by day, each cell in words as well as colour. */
export const Heatmap: Story = card(<HeatmapView heatmap={heatmap} />);
export const HeatmapEmpty: Story = card(<HeatmapView heatmap={heatmapEmpty} />);
export const Distribution: Story = card(<DistributionView distribution={distribution} />);
export const DistributionEmpty: Story = card(<DistributionView distribution={distributionEmpty} />);
/** Who sees whom, with two rules. */
export const VisibilityRules: Story = card(<VisibilityView rules={visibilityRules} />);
/** Who sees whom, open. */
export const VisibilityOpen: Story = card(<VisibilityView rules={visibilityOpen} />);
/** What a narrowed reader is told. */
export const VisibilityNotice: Story = { render: () => <VisibilityNote visibility={presetRestricted.visibility} /> };
/** The pipeline by region: the funnel with conversion and movement, then stage by region. */
export const Pipeline: Story = card(<PipelineView pipeline={pipeline} />);
/** Drilled to one branch's RMs. */
export const PipelineRms: Story = card(<PipelineView pipeline={pipelineRms} />);
export const PipelineNoStages: Story = card(<PipelineView pipeline={pipelineNoStages} />);
/** Stages set up, nothing loaded yet this month. */
export const PipelineEmpty: Story = card(<PipelineView pipeline={pipelineEmpty} />);
/** The stages in funnel order, and the form to add one. */
export const PipelineStages: Story = card(<PipelineStagesView stages={pipelineStages} />);
export const PipelineStagesNone: Story = card(<PipelineStagesView stages={pipelineStagesNone} />);
/** No snapshot metric registered to build a stage from. */
export const PipelineStagesNoMetrics: Story = card(<PipelineStagesView stages={pipelineStagesNoMetrics} />);
