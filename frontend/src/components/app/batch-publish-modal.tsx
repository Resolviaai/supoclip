"use client";

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Calendar,
  Check,
  ChevronDown,
  ChevronUp,
  Clock,
  Layers,
  Loader2,
  Send,
  Sparkles,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SocialProviderIcon } from "@/components/social-provider-icon";
import { ClipCover, ScoreRing } from "@/components/app/clip-cover";
import { getClipUrl } from "@/lib/clip-actions";
import { formatClipDuration } from "@/lib/generations";
import {
  PROVIDER_LABELS,
  formatDateTime,
  generateDripSchedule,
  parseHashtagInput,
  type DripStrategy,
  type SocialConnection,
  type SocialPrivacyLevel,
  type SocialProviderStatus,
} from "@/lib/social";
import { cn } from "@/lib/utils";

export interface BatchPublishClip {
  id: string;
  filename: string;
  video_url: string;
  duration: number;
  virality_score: number;
  hook_title: string | null;
  text: string | null;
  clip_order: number;
}

interface PerClipConfig {
  clipId: string;
  title: string;
  caption: string;
  hashtags: string;
  selectedAccountIds: string[];
  privacyByAccount: Record<string, SocialPrivacyLevel>;
  scheduledFor: string | null;
  isExpanded: boolean;
}

interface BatchPublishModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  taskId: string;
  clips: BatchPublishClip[];
  connections: SocialConnection[];
  providers: SocialProviderStatus[];
  onPublished: () => void | Promise<void>;
}

export function BatchPublishModal({
  open,
  onOpenChange,
  taskId,
  clips,
  connections,
  providers,
  onPublished,
}: BatchPublishModalProps) {
  const activeConnections = useMemo(
    () =>
      connections.filter(
        (connection) =>
          connection.status !== "revoked" &&
          connection.status !== "reauth_required" &&
          providers.some(
            (provider) => provider.provider === connection.provider && provider.configured,
          ),
      ),
    [connections, providers],
  );

  const [masterAccountIds, setMasterAccountIds] = useState<string[]>([]);
  const [strategy, setStrategy] = useState<DripStrategy>("daily_peak");
  const [clipConfigs, setClipConfigs] = useState<Record<string, PerClipConfig>>({});
  const [isGeneratingCaptions, setIsGeneratingCaptions] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [progressCount, setProgressCount] = useState(0);
  const [totalSubmissions, setTotalSubmissions] = useState(0);

  // Initialize master accounts and per-clip configurations on open
  useEffect(() => {
    if (!open) return;

    const defaultIds = activeConnections.map((c) => c.id);
    setMasterAccountIds(defaultIds);

    const initialSchedule = generateDripSchedule(clips.length, strategy);

    const initialConfigs: Record<string, PerClipConfig> = {};
    clips.forEach((clip, index) => {
      const defaultTitle = clip.hook_title || `Clip ${clip.clip_order}`;
      const defaultCaption = clip.text || "";

      const initialPrivacy: Record<string, SocialPrivacyLevel> = {};
      activeConnections.forEach((conn) => {
        initialPrivacy[conn.id] = "public";
      });

      initialConfigs[clip.id] = {
        clipId: clip.id,
        title: defaultTitle,
        caption: defaultCaption,
        hashtags: "#shorts #viral",
        selectedAccountIds: [...defaultIds],
        privacyByAccount: initialPrivacy,
        scheduledFor: initialSchedule[index] ?? null,
        isExpanded: index === 0, // First clip expanded by default
      };
    });

    setClipConfigs(initialConfigs);
    setIsSubmitting(false);
    setProgressCount(0);
  }, [open, clips, activeConnections, strategy]);

  // Recalculate schedule dates whenever strategy changes
  const handleStrategyChange = (newStrategy: DripStrategy) => {
    setStrategy(newStrategy);
    const newDates = generateDripSchedule(clips.length, newStrategy);
    setClipConfigs((current) => {
      const updated = { ...current };
      clips.forEach((clip, index) => {
        if (updated[clip.id]) {
          updated[clip.id] = {
            ...updated[clip.id],
            scheduledFor: newDates[index] ?? null,
          };
        }
      });
      return updated;
    });
  };

  // Toggle master accounts and synchronize to individual clips
  const toggleMasterAccount = (accountId: string) => {
    const isSelected = masterAccountIds.includes(accountId);
    const nextIds = isSelected
      ? masterAccountIds.filter((id) => id !== accountId)
      : [...masterAccountIds, accountId];

    setMasterAccountIds(nextIds);
    setClipConfigs((current) => {
      const updated = { ...current };
      clips.forEach((clip) => {
        if (updated[clip.id]) {
          updated[clip.id] = {
            ...updated[clip.id],
            selectedAccountIds: [...nextIds],
          };
        }
      });
      return updated;
    });
  };

  const toggleSelectAllAccounts = () => {
    const allSelected = masterAccountIds.length === activeConnections.length;
    const nextIds = allSelected ? [] : activeConnections.map((c) => c.id);
    setMasterAccountIds(nextIds);
    setClipConfigs((current) => {
      const updated = { ...current };
      clips.forEach((clip) => {
        if (updated[clip.id]) {
          updated[clip.id] = {
            ...updated[clip.id],
            selectedAccountIds: [...nextIds],
          };
        }
      });
      return updated;
    });
  };

  // Toggle account specifically for one clip
  const toggleClipAccount = (clipId: string, accountId: string) => {
    setClipConfigs((current) => {
      const config = current[clipId];
      if (!config) return current;
      const isSelected = config.selectedAccountIds.includes(accountId);
      const nextAccounts = isSelected
        ? config.selectedAccountIds.filter((id) => id !== accountId)
        : [...config.selectedAccountIds, accountId];
      return {
        ...current,
        [clipId]: {
          ...config,
          selectedAccountIds: nextAccounts,
        },
      };
    });
  };

  // Update specific fields of a clip config
  const updateClipField = <K extends keyof PerClipConfig>(
    clipId: string,
    field: K,
    value: PerClipConfig[K],
  ) => {
    setClipConfigs((current) => {
      const config = current[clipId];
      if (!config) return current;
      return {
        ...current,
        [clipId]: {
          ...config,
          [field]: value,
        },
      };
    });
  };

  // Batch AI Caption generation across all selected clips
  const handleGenerateAllCaptions = async () => {
    setIsGeneratingCaptions(true);
    const primaryProvider = activeConnections[0]?.provider || "instagram";

    try {
      const promises = clips.map(async (clip) => {
        try {
          const res = await fetch("/api/social/captions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              task_id: taskId,
              clip_id: clip.id,
              provider: primaryProvider,
            }),
          });
          if (!res.ok) return null;
          const data = await res.json();
          return { clipId: clip.id, caption: data.caption as string };
        } catch {
          return null;
        }
      });

      const results = await Promise.all(promises);
      setClipConfigs((current) => {
        const updated = { ...current };
        results.forEach((item) => {
          if (item && item.caption && updated[item.clipId]) {
            updated[item.clipId] = {
              ...updated[item.clipId],
              caption: item.caption,
            };
          }
        });
        return updated;
      });

      toast.success("AI captions generated for all selected clips");
    } catch {
      toast.error("Failed to generate some captions. You can write custom captions below.");
    } finally {
      setIsGeneratingCaptions(false);
    }
  };

  // Calculate total posts count
  const totalTargetPosts = useMemo(() => {
    return Object.values(clipConfigs).reduce(
      (sum, config) => sum + config.selectedAccountIds.length,
      0,
    );
  }, [clipConfigs]);

  // Master Submit
  const handleBatchPublish = async () => {
    if (isSubmitting || totalTargetPosts === 0) return;

    setIsSubmitting(true);
    setTotalSubmissions(totalTargetPosts);
    setProgressCount(0);

    let successfulPosts = 0;
    let failedPosts = 0;

    for (const clip of clips) {
      const config = clipConfigs[clip.id];
      if (!config || config.selectedAccountIds.length === 0) continue;

      for (const accountId of config.selectedAccountIds) {
        const connection = activeConnections.find((c) => c.id === accountId);
        if (!connection) continue;

        try {
          const res = await fetch("/api/social/posts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              task_id: taskId,
              clip_id: clip.id,
              social_account_id: accountId,
              title: config.title.trim(),
              caption: config.caption,
              hashtags: parseHashtagInput(config.hashtags),
              privacy_level: config.privacyByAccount[accountId] || "public",
              ...(config.scheduledFor ? { scheduled_for: config.scheduledFor } : {}),
            }),
          });

          if (res.ok) {
            successfulPosts++;
          } else {
            failedPosts++;
          }
        } catch {
          failedPosts++;
        } finally {
          setProgressCount((prev) => prev + 1);
        }
      }
    }

    if (successfulPosts > 0) {
      if (failedPosts > 0) {
        toast.warning(
          `Scheduled ${successfulPosts} post${successfulPosts > 1 ? "s" : ""}, but ${failedPosts} failed.`,
        );
      } else {
        toast.success(
          `Successfully scheduled ${successfulPosts} post${successfulPosts > 1 ? "s" : ""} across ${clips.length} clips`,
        );
      }
      try {
        await onPublished();
      } catch {
        // Handled silently
      }
      onOpenChange(false);
    } else {
      toast.error(
        failedPosts > 0
          ? `Failed to publish ${failedPosts} post${failedPosts > 1 ? "s" : ""}. Please check your connection.`
          : "Failed to publish posts. Please check your social accounts connection.",
      );
    }

    setIsSubmitting(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "gap-0 overflow-hidden p-0 duration-200 flex flex-col bg-background shadow-2xl border",
          "max-md:fixed max-md:inset-0 max-md:top-0 max-md:left-0 max-md:h-dvh max-md:max-h-dvh max-md:w-full max-md:max-w-none max-md:translate-x-0 max-md:translate-y-0 max-md:rounded-none max-md:border-none",
          "md:max-w-3xl md:max-h-[90dvh] md:rounded-2xl",
        )}
      >
        {/* Header */}
        <DialogHeader className="p-4 sm:p-6 border-b shrink-0 bg-muted/20 pt-[max(1rem,env(safe-area-inset-top))] sm:pt-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5 sm:gap-3">
              <span className="flex size-8 sm:size-9 items-center justify-center rounded-xl bg-primary/10 text-primary border border-primary/20 shrink-0">
                <Layers className="size-4 sm:size-5" />
              </span>
              <div>
                <DialogTitle className="text-lg sm:text-xl font-bold tracking-tight">
                  Batch Publish & Schedule
                </DialogTitle>
                <DialogDescription className="text-[11px] sm:text-xs text-muted-foreground mt-0.5 line-clamp-1 sm:line-clamp-none">
                  Publish {clips.length} selected clips across your social channels with anti-bot scheduling.
                </DialogDescription>
              </div>
            </div>
            <Badge variant="outline" className="gap-1 px-2 py-0.5 sm:px-2.5 sm:py-1 text-[11px] sm:text-xs font-semibold tabular-nums shrink-0">
              {clips.length} selected
            </Badge>
          </div>
        </DialogHeader>

        {/* Scrollable Body */}
        <div className="flex-1 overflow-y-auto p-3.5 sm:p-6 space-y-4 sm:space-y-6 overscroll-contain">
          {/* Master Controls Container */}
          <div className="rounded-xl border bg-muted/10 p-3.5 sm:p-4 space-y-3.5 sm:space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-[11px] sm:text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Master Defaults (All selected clips)
              </span>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={handleGenerateAllCaptions}
                disabled={isGeneratingCaptions || isSubmitting}
                className="h-8 text-xs gap-1.5 text-primary hover:text-primary hover:bg-primary/10 active:scale-95"
              >
                {isGeneratingCaptions ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Sparkles className="size-3.5" />
                )}
                Auto-Draft Captions
              </Button>
            </div>

            {/* Global Platforms Row */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <Label className="text-xs font-medium text-foreground">Target Platforms</Label>
                <button
                  type="button"
                  onClick={toggleSelectAllAccounts}
                  className="text-[11px] text-muted-foreground hover:text-foreground transition-colors p-1"
                >
                  {masterAccountIds.length === activeConnections.length
                    ? "Deselect All"
                    : "Select All"}
                </button>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {activeConnections.map((connection) => {
                  const isSelected = masterAccountIds.includes(connection.id);
                  return (
                    <button
                      key={connection.id}
                      type="button"
                      onClick={() => toggleMasterAccount(connection.id)}
                      className={cn(
                        "flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-xs font-medium transition-all text-left active:scale-98 min-h-[44px]",
                        isSelected
                          ? "border-primary/50 bg-primary/10 text-foreground shadow-xs ring-1 ring-primary/20"
                          : "border-border/40 bg-background text-muted-foreground hover:border-border hover:text-foreground",
                      )}
                    >
                      <SocialProviderIcon provider={connection.provider} className="size-4 shrink-0" />
                      <span className="truncate flex-1">{PROVIDER_LABELS[connection.provider]}</span>
                      {isSelected && <Check className="size-3.5 text-primary shrink-0" />}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Smart Auto-Drip Strategy */}
            <div>
              <Label className="text-xs font-medium text-foreground block mb-2">
                Anti-Bot Scheduling Strategy
              </Label>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                <button
                  type="button"
                  onClick={() => handleStrategyChange("daily_peak")}
                  className={cn(
                    "rounded-lg border p-3 text-left text-xs transition-all active:scale-98 min-h-[44px]",
                    strategy === "daily_peak"
                      ? "border-primary/50 bg-primary/10 text-foreground ring-1 ring-primary/20"
                      : "border-border/40 bg-background text-muted-foreground hover:text-foreground",
                  )}
                >
                  <div className="flex items-center gap-1.5 font-semibold text-foreground">
                    <Calendar className="size-3.5 text-primary shrink-0" />
                    Daily Drip (Peak Hours)
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-1">
                    1 clip/day at 6:30 PM with randomized human jitter (±6m).
                  </p>
                </button>

                <button
                  type="button"
                  onClick={() => handleStrategyChange("every_3_hours")}
                  className={cn(
                    "rounded-lg border p-3 text-left text-xs transition-all active:scale-98 min-h-[44px]",
                    strategy === "every_3_hours"
                      ? "border-primary/50 bg-primary/10 text-foreground ring-1 ring-primary/20"
                      : "border-border/40 bg-background text-muted-foreground hover:text-foreground",
                  )}
                >
                  <div className="flex items-center gap-1.5 font-semibold text-foreground">
                    <Clock className="size-3.5 text-primary shrink-0" />
                    Every 3 Hours
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-1">
                    Spaces clips evenly across today with natural timing offsets.
                  </p>
                </button>

                <button
                  type="button"
                  onClick={() => handleStrategyChange("immediate")}
                  className={cn(
                    "rounded-lg border p-3 text-left text-xs transition-all active:scale-98 min-h-[44px]",
                    strategy === "immediate"
                      ? "border-primary/50 bg-primary/10 text-foreground ring-1 ring-primary/20"
                      : "border-border/40 bg-background text-muted-foreground hover:text-foreground",
                  )}
                >
                  <div className="flex items-center gap-1.5 font-semibold text-foreground">
                    <Send className="size-3.5 text-primary shrink-0" />
                    Immediate (Buffered)
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-1">
                    Queues immediately with 90s anti-burst rate limiting.
                  </p>
                </button>
              </div>
            </div>
          </div>

          {/* Granular Per-Clip Review Cards */}
          <div className="space-y-3">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground block">
              Granular Clip Controls ({clips.length} clips)
            </span>

            {clips.map((clip) => {
              const config = clipConfigs[clip.id];
              if (!config) return null;

              return (
                <div
                  key={clip.id}
                  className="rounded-xl border bg-card text-card-foreground shadow-xs overflow-hidden transition-all"
                >
                  {/* Card Bar */}
                  <div
                    onClick={() => updateClipField(clip.id, "isExpanded", !config.isExpanded)}
                    className="flex items-center gap-3 p-3.5 cursor-pointer hover:bg-muted/30 transition-colors select-none"
                  >
                    <ClipCover
                      src={getClipUrl(clip.video_url, clip.filename)}
                      className="size-12 rounded-lg shrink-0 aspect-[9/16]"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-semibold truncate max-w-sm">
                          {config.title || `Clip ${clip.clip_order}`}
                        </span>
                        <ScoreRing score={clip.virality_score} size={28} />
                        <span className="text-xs text-muted-foreground tabular-nums">
                          {formatClipDuration(clip.duration)}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 mt-1 text-xs text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <Send className="size-3" />
                          {config.selectedAccountIds.length} platforms
                        </span>
                        <span>·</span>
                        <span className="flex items-center gap-1 truncate">
                          <Clock className="size-3" />
                          {config.scheduledFor
                            ? formatDateTime(config.scheduledFor)
                            : "Immediate (Buffered)"}
                        </span>
                      </div>
                    </div>
                    <Button size="icon-sm" variant="ghost" className="size-8 text-muted-foreground">
                      {config.isExpanded ? (
                        <ChevronUp className="size-4" />
                      ) : (
                        <ChevronDown className="size-4" />
                      )}
                    </Button>
                  </div>

                  {/* Expanded Customizer */}
                  {config.isExpanded && (
                    <div className="p-4 pt-2 border-t bg-muted/5 space-y-4 text-xs">
                      {/* Per-Clip Platform Override */}
                      <div>
                        <Label className="text-[11px] font-medium text-muted-foreground block mb-1.5">
                          Publish this specific clip to:
                        </Label>
                        <div className="flex flex-wrap gap-1.5">
                          {activeConnections.map((connection) => {
                            const isIncluded = config.selectedAccountIds.includes(connection.id);
                            return (
                              <button
                                key={connection.id}
                                type="button"
                                onClick={() => toggleClipAccount(clip.id, connection.id)}
                                className={cn(
                                  "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs transition-all active:scale-95",
                                  isIncluded
                                    ? "border-primary/50 bg-primary/10 text-foreground font-medium"
                                    : "border-border/40 bg-background text-muted-foreground opacity-60 hover:opacity-100",
                                )}
                              >
                                <SocialProviderIcon provider={connection.provider} className="size-3.5" />
                                <span>{PROVIDER_LABELS[connection.provider]}</span>
                                {isIncluded && <Check className="size-3 text-primary" />}
                              </button>
                            );
                          })}
                        </div>
                      </div>

                      {/* Title & Caption */}
                      <div className="grid grid-cols-1 gap-2.5">
                        <div>
                          <Label className="text-[11px] font-medium text-muted-foreground block mb-1">
                            Title / Hook
                          </Label>
                          <Input
                            value={config.title}
                            onChange={(e) => updateClipField(clip.id, "title", e.target.value)}
                            className="h-8 text-xs bg-background"
                            maxLength={100}
                          />
                        </div>
                        <div>
                          <Label className="text-[11px] font-medium text-muted-foreground block mb-1">
                            Caption
                          </Label>
                          <Textarea
                            value={config.caption}
                            onChange={(e) => updateClipField(clip.id, "caption", e.target.value)}
                            rows={2}
                            className="text-xs min-h-[64px] bg-background resize-y"
                          />
                        </div>
                      </div>

                      {/* Scheduled Time & Privacy */}
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
                        <div>
                          <Label className="text-[11px] font-medium text-muted-foreground block mb-1">
                            Scheduled Date & Time (Human Jitter Offset)
                          </Label>
                          <Input
                            type="datetime-local"
                            value={
                              config.scheduledFor
                                ? new Date(config.scheduledFor).toISOString().slice(0, 16)
                                : ""
                            }
                            onChange={(e) => {
                              const val = e.target.value;
                              updateClipField(
                                clip.id,
                                "scheduledFor",
                                val ? new Date(val).toISOString() : null,
                              );
                            }}
                            className="h-8 text-xs bg-background tabular-nums"
                          />
                        </div>

                        <div>
                          <Label className="text-[11px] font-medium text-muted-foreground block mb-1">
                            Visibility
                          </Label>
                          <Select
                            value={config.privacyByAccount[activeConnections[0]?.id || ""] || "public"}
                            onValueChange={(val: SocialPrivacyLevel) => {
                              const updatedPrivacy: Record<string, SocialPrivacyLevel> = {};
                              activeConnections.forEach((conn) => {
                                updatedPrivacy[conn.id] = val;
                              });
                              updateClipField(clip.id, "privacyByAccount", updatedPrivacy);
                            }}
                          >
                            <SelectTrigger className="h-8 text-xs bg-background">
                              <SelectValue placeholder="Public" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="public">Public</SelectItem>
                              <SelectItem value="unlisted">Unlisted</SelectItem>
                              <SelectItem value="private">Private</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Footer with mobile safe area bottom */}
        <DialogFooter className="p-3.5 sm:p-4 sm:px-6 border-t shrink-0 bg-muted/20 flex flex-col sm:flex-row items-center justify-between gap-3 pb-[max(1rem,env(safe-area-inset-bottom))] sm:pb-4">
          <div className="text-xs text-muted-foreground tabular-nums w-full sm:w-auto text-center sm:text-left">
            {isSubmitting ? (
              <span className="flex items-center justify-center sm:justify-start gap-2">
                <Loader2 className="size-3.5 animate-spin text-primary shrink-0" />
                Dispatching {progressCount} of {totalSubmissions} target posts...
              </span>
            ) : (
              <span>
                Total dispatch: <strong>{totalTargetPosts} posts</strong> across {clips.length} clips
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onOpenChange(false)}
              disabled={isSubmitting}
              className="flex-1 sm:flex-none h-11 sm:h-9 text-xs active:scale-95 transition-transform"
            >
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={handleBatchPublish}
              disabled={isSubmitting || totalTargetPosts === 0}
              className="flex-1 sm:flex-none h-11 sm:h-9 text-xs gap-1.5 active:scale-95 transition-transform"
            >
              {isSubmitting ? (
                <Loader2 className="size-4 animate-spin shrink-0" />
              ) : (
                <Send className="size-4 shrink-0" />
              )}
              Publish {clips.length} Clips ({totalTargetPosts} Posts)
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
