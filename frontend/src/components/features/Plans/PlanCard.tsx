import React, { useState } from "react";
import { Card, Modal, Tooltip, Input, Button } from "antd";
import { PlayCircle, Edit2, Clock, Trash2, Download } from "lucide-react";
import { planAPI } from "../../views/api";
import PlanView from "../../views/chat/plan";
import { getRelativeTimeString } from "../../views/atoms";
import { IPlan, IPlanStep } from "../../types/plan";
import { useTranslation } from "react-i18next";

interface PlanCardProps {
  plan: IPlan;
  onUsePlan?: (plan: IPlan) => void;
  onEditClick?: (plan: IPlan) => void;
  onPlanSaved?: (updatedPlan: IPlan) => void;
  onDeletePlan?: (planId: number) => void;
  isNew?: boolean;
  onEditComplete?: () => void;
}

const PlanCard: React.FC<PlanCardProps> = ({
  plan,
  onUsePlan,
  onEditClick,
  onPlanSaved,
  onDeletePlan,
  isNew = false,
  onEditComplete,
}) => {
  const { t, i18n } = useTranslation();
  const [isHovering, setIsHovering] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(isNew);
  const [localSteps, setLocalSteps] = useState<IPlanStep[]>(plan.steps || []);
  const [localTask, setLocalTask] = useState(plan.task || "");
  const [isAutoSaving, setIsAutoSaving] = useState(false);

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();

    try {
      if (!plan.id || !plan.user_id) {
        console.error("Missing required IDs:", {
          planId: plan.id,
          userId: plan.user_id,
        });
        return;
      }

      if (window.confirm(`"${t("Are you sure you want to delete this plan")}" "${plan.task}"?`)) {
        await planAPI.deletePlan(plan.id, plan.user_id);

        if (onDeletePlan) {
          onDeletePlan(plan.id);
        }
      }
    } catch (error) {
      console.log("Failed to delete plan:", error);
    }
  };

  const handleEdit = () => {
    setIsModalOpen(true);
    if (onEditClick) {
      onEditClick(plan);
    }
  };

  const handleModalCancel = () => {
    // Save any changes before closing the modal
    const updatedPlan: IPlan = {
      ...plan,
      task: localTask,
      steps: localSteps,
    };

    const hasChanges =
      localTask !== plan.task ||
      JSON.stringify(localSteps) !== JSON.stringify(plan.steps);

    if (hasChanges) {
      if (plan.id !== undefined && plan.user_id !== undefined) {
        planAPI
          .updatePlan(plan.id, updatedPlan, plan.user_id)
          .then(() => {
            // notify parent to update the card
            if (onPlanSaved) {
              onPlanSaved(updatedPlan);
            }
          })
          .catch((error) => {
            console.error("Failed to save plan on close:", error);
          });
      }
    }

    setIsModalOpen(false);
    if (isNew && onEditComplete) {
      onEditComplete();
    }
  };

  const handleSavePlan = async (
    updatedSteps: IPlanStep[],
    isAutoSave = false
  ) => {
    try {
      if (isAutoSave) {
        setIsAutoSaving(true);
      }

      const updatedPlan: IPlan = {
        ...plan,
        task: localTask,
        steps: updatedSteps,
      };

      if (plan.id === undefined || plan.user_id === undefined) {
        console.error("Cannot update plan: missing IDs");
        return;
      }

      await planAPI.updatePlan(plan.id, updatedPlan, plan.user_id);

      if (onPlanSaved && !isAutoSave && !isAutoSaving) {
        onPlanSaved(updatedPlan);
      }

      setIsAutoSaving(false);
    } catch (error) {
      console.error("Failed to save plan:", error);
      setIsAutoSaving(false);
    }
  };

  const handleExport = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();

    try {
      const planData = JSON.stringify(plan, null, 2);
      const blob = new Blob([planData], { type: "application/json" });

      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `plan-${plan.id}-${plan.task
        .replace(/\s+/g, "-")
        .toLowerCase()}.json`;
      document.body.appendChild(link);
      link.click();

      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (error) {
      console.error("Failed to export plan:", error);
    }
  };

  const steps = plan.steps || [];
  return (
    <>
      <Card
        key={plan.id}
        title={
          <div className="flex justify-between items-center">
            <span
              className="truncate max-w-[80%]"
              title={plan.task || t("Untitled Plan")}
            >
              {plan.task || t("Untitled Plan")}
            </span>
            {isHovering && (
              <div className="flex items-center ml-2">
                <Tooltip title={t("Export plan as JSON file")}>
                  <button
                    className="bg-transparent border-none cursor-pointer mr-2"
                    onClick={handleExport}
                    aria-label={t("Export plan")}
                  >
                    <Download className="h-5 w-5 transition-colors" />
                  </button>
                </Tooltip>
                <Tooltip title={t("Delete this plan")}>
                  <button
                    className="bg-transparent border-none cursor-pointer"
                    onClick={handleDelete}
                    aria-label={t("Delete plan")}
                  >
                    <Trash2 className="h-5 w-5 transition-colors" />
                  </button>
                </Tooltip>
              </div>
            )}
          </div>
        }
        className="shadow-md hover:shadow-lg transition-shadow duration-200 flex flex-col"
        onMouseEnter={() => setIsHovering(true)}
        onMouseLeave={() => setIsHovering(false)}
        actions={[
          <div key="use" className="flex items-center justify-center h-full">
            <Tooltip title={t("Create a new session with this plan loaded")}>
              <Button
                type="text"
                className="cursor-pointer flex items-center justify-center font-semibold transition-colors"
                onClick={() => {
                  if (onUsePlan) onUsePlan(plan);
                }}
              >
                <PlayCircle className="h-4 w-4 mr-1" />
                {t("Run Plan")}
              </Button>
            </Tooltip>
          </div>,
          <div key="edit" className="flex items-center justify-center h-full">
            <Tooltip title={t("Modify plan title and steps")}>
              <Button
                type="text"
                className="cursor-pointer flex items-center justify-center font-semibold transition-colors"
                onClick={handleEdit}
              >
                <Edit2 className="h-4 w-4 mr-1" />
                {t("Edit")}
              </Button>
            </Tooltip>
          </div>,
        ]}
      >
        <div className="flex flex-col flex-grow justify-between">
          <div>
            <div className="mb-4">
              <p className="text-sm">{steps.length} {t("steps")}</p>
            </div>

            <div className="space-y-2 min-h-[80px]">
              {steps.slice(0, 3).map((step, idx) => (
                <div
                  key={idx}
                  className="text-xs border-l-2 border-gray-200 pl-2"
                >
                  {step.title || `Step ${idx + 1}`}
                </div>
              ))}
              {steps.length > 3 && (
                <div className="text-xs">
                  + {steps.length - 3} {t("more steps")}
                </div>
              )}
            </div>
          </div>

          <div className="mt-4 text-xs flex items-center">
            {plan.created_at ? (
              <>
                <Clock className="h-3 w-3 mr-1" />
                {getRelativeTimeString(plan.created_at)}
              </>
            ) : (
              ""
            )}
          </div>
        </div>
      </Card>

      <Modal
        open={isModalOpen}
        onCancel={handleModalCancel}
        footer={null}
        width={800}
        destroyOnClose
      >
        {isModalOpen && (
          <div>
            <div className="mb-4">
              <label className="block text-sm font-medium mb-1">
                {t("Plan Title")}
              </label>
              <Input
                type="text"
                value={localTask}
                onChange={(e) => setLocalTask(e.target.value)}
                onPressEnter={() => handleSavePlan(localSteps, false)}
                placeholder={t("Enter plan title")}
              />
            </div>
            <PlanView
              task={localTask}
              plan={localSteps}
              setPlan={setLocalSteps}
              viewOnly={false}
              onSavePlan={(updatedSteps) => {
                handleSavePlan(updatedSteps, true);
              }}
            />
          </div>
        )}
      </Modal>
    </>
  );
};

export default PlanCard;
