import React from "react";
import { Divider, Tooltip, Select, Flex, Switch } from "antd";
import { InfoCircleOutlined, MoonFilled, SunFilled } from "@ant-design/icons";
import AllowedWebsitesList from "./AllowedWebsitesList";
import { useTranslation } from "react-i18next";

interface GeneralSettingsProps {
  config: any;
  darkMode: string;
  setDarkMode: (mode: string) => void;
  handleUpdateConfig: (changes: any) => void;
}

const GeneralSettings: React.FC<GeneralSettingsProps> = ({
  config,
  darkMode,
  setDarkMode,
  handleUpdateConfig,
}) => {
  const { t, i18n } = useTranslation();
  return (
    <Flex vertical gap="small">
      {/* Dark Mode Toggle */}
      <Flex align="center" justify="space-between">
        <span>
          {darkMode === "dark" ? t("Dark Mode") : t("Light Mode")}
        </span>
        <button
          onClick={() => setDarkMode(darkMode === "dark" ? "light" : "dark")}
        >
          {darkMode === "dark" ? (
            <MoonFilled className="w-6 h-6" />
          ) : (
            <SunFilled className="w-6 h-6" />
          )}
        </button>
      </Flex>

      <Divider style={{ margin: "0px" }} />

      {/* Basic Settings */}
      <Flex vertical gap="small">
        <Flex align="center" justify="space-between" wrap>
          <Flex align="center" justify="start" gap="small">
            {t("Action Approval Policy")}
            <Tooltip title={t("Controls when approval is required before taking actions")}>
              <InfoCircleOutlined className="text-secondary hover:text-primary cursor-help" />
            </Tooltip>
          </Flex>
          <Select
            value={config.approval_policy}
            onChange={(value) => handleUpdateConfig({ approval_policy: value })}
            options={[
              { value: "never", label: t("Never require approval") },
              { value: "auto-conservative", label: t("AI based judgement") },
              { value: "always", label: t("Always require approval") },
            ]}
          />
        </Flex>

        <Divider style={{ margin: "0px" }} />

        <AllowedWebsitesList
          config={config}
          handleUpdateConfig={handleUpdateConfig}
        />

        <Divider style={{ margin: "0px" }} />
        <Flex vertical gap="small">
          <Flex align="center" justify="space-between" wrap gap="large">
            <Flex align="center" justify="start" gap="small" wrap>
              {t("Allow Replans")}
              <Tooltip title={t("When enabled, Agents will automatically replan if the current plan is not working or you change the original request")}>
                <InfoCircleOutlined className="text-secondary hover:text-primary cursor-help" />
              </Tooltip>
            </Flex>
            <Switch
              checked={config.allow_for_replans}
              checkedChildren="ON"
              unCheckedChildren="OFF"
              onChange={(checked) => handleUpdateConfig({ allow_for_replans: checked })}
            />
          </Flex>
          <Divider style={{ margin: "0px" }} />
          <Flex align="center" justify="space-between" wrap gap="large">
            <Flex align="center" justify="start" gap="small" wrap>
              {t("Browser Headless")}
              <Tooltip title={t("Only applicable when running without docker. When enabled, the browser will run in headless mode (no UI).")}>
                <InfoCircleOutlined className="text-secondary hover:text-primary cursor-help" />
              </Tooltip>
            </Flex>
            <Switch
              checked={config.browser_headless}
              checkedChildren="ON"
              unCheckedChildren="OFF"
              onChange={(checked) =>
                handleUpdateConfig({ browser_headless: checked })
              }
            />
          </Flex>
          <Divider style={{ margin: "0px" }} />

          <Flex align="center" justify="space-between" wrap gap="large">
            <Flex align="center" justify="start" gap="small" wrap>
              {t("Enable Sentinel Steps")}
              <Tooltip title={t("When enabled, sentinel plan steps will be included in the execution process")}>
                <InfoCircleOutlined className="text-secondary hover:text-primary cursor-help" />
              </Tooltip>
            </Flex>
            <Switch
              checked={config.sentinel_plan?.enable_sentinel_steps || false}
              checkedChildren="ON"
              unCheckedChildren="OFF"
              onChange={(checked) =>
                handleUpdateConfig({
                  sentinel_plan: {
                    ...config.sentinel_plan,
                    enable_sentinel_steps: checked
                  }
                })
              }
            />
          </Flex>
          <Divider style={{ margin: "0px" }} />

          <Flex align="center" justify="space-between" wrap gap="small">
            <Flex align="center" gap="small">
              {t("Retrieve Relevant Plans")}
              <Tooltip title={t("Controls how Agents retrieves and uses relevant plans from previous sessions")}>
                <InfoCircleOutlined className="text-secondary hover:text-primary cursor-help" />
              </Tooltip>
            </Flex>
            <Select
              value={config.retrieve_relevant_plans}
              onChange={(value) => handleUpdateConfig({ retrieve_relevant_plans: value })}
              options={[
                { value: "never", label: t("No plan retrieval") },
                { value: "hint", label: t("Retrieve plans as hints") },
                { value: "reuse", label: t("Retrieve plans to use directly") },
              ]}
            />
          </Flex>
        </Flex>
      </Flex>
    </Flex>
  );
};

export default GeneralSettings;
