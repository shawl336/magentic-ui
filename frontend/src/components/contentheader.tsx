import React from "react";
import { PanelLeftClose, PanelLeftOpen, Plus } from "lucide-react";
import { Tooltip } from "antd";
import { appContext } from "../hooks/provider";
import { useConfigStore } from "../hooks/store";
import { Settings } from "lucide-react";
import SignInModal from "./signin";
import SettingsModal from "./settings/SettingsModal";
import logo from "../assets/logo.svg";
import { Button } from "./common/Button";
import { useTranslation } from "react-i18next";

type ContentHeaderProps = {
  onMobileMenuToggle: () => void;
  isMobileMenuOpen: boolean;
  isSidebarOpen: boolean;
  onToggleSidebar: () => void;
  onNewSession: () => void;
};

const ContentHeader = ({
  isSidebarOpen,
  onToggleSidebar,
  onNewSession,
}: ContentHeaderProps) => {
  const { t, i18n } = useTranslation();
  const { user } = React.useContext(appContext);
  useConfigStore();
  const [isEmailModalOpen, setIsEmailModalOpen] = React.useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = React.useState(false);

  return (
    <div className="sticky top-0 pl-2 pr-4 bg-gradient-to-r from-gray-200 via-gray-100 to-secondary shadow-lg border-b border-gray-400/30 backdrop-blur-sm">
      <div className="flex h-16 items-center justify-between">
        {/* Left side: Text and Sidebar Controls */}
        <div className="flex items-center">
          {/* Sidebar Toggle */}
          <Tooltip title={isSidebarOpen ? t("Close Sidebar") : t("Open Sidebar")}>
            <Button
              variant="tertiary"
              size="sm"
              icon={
                isSidebarOpen ? (
                  <PanelLeftClose strokeWidth={1.5} className="h-6 w-6" />
                ) : (
                  <PanelLeftOpen strokeWidth={1.5} className="h-6 w-6" />
                )
              }
              onClick={onToggleSidebar}
              className="!px-0 transition-colors hover:text-gray-700 hover:bg-gray-400/30 rounded-lg"
            />
          </Tooltip>

          {/* New Session Button */}
          <div className="w-[40px]">
            {!isSidebarOpen && (
              <Tooltip title={t("Create New Session")}>
                <Button
                  variant="tertiary"
                  size="sm"
                  icon={<Plus className="w-6 h-6" />}
                  onClick={onNewSession}
                  className="transition-colors hover:text-gray-700 hover:bg-gray-400/30 rounded-lg"
                />
              </Tooltip>
            )}
          </div>
          <div className="flex items-center space-x-2">
            <img src={logo} alt="Magentic-UI Logo" className="h-28 w-28" />
            <div className="text-gray-800 text-2xl font-bold drop-shadow-sm">{t("Magentic-UI")}</div>
          </div>
        </div>

        {/* User Profile and Settings */}
        <div className="flex items-center space-x-4">
          {/* User Profile */}
          {user && (
            <Tooltip title="View or update your profile">
              <div
                className="flex items-center space-x-2 cursor-pointer hover:bg-gray-400/20 rounded-lg p-1 transition-colors"
                onClick={() => setIsEmailModalOpen(true)}
              >
                {user.avatar_url ? (
                  <img
                    className="h-8 w-8 rounded-full"
                    src={user.avatar_url}
                    alt={user.name}
                  />
                ) : (
                  <div className="bg-gray-500 h-8 w-8 rounded-full flex items-center justify-center text-white font-semibold hover:bg-gray-600 transition-colors">
                    {user.name?.[0]}
                  </div>
                )}
              </div>
            </Tooltip>
          )}

          {/* Settings Button */}
          <div className="text-primary">
            <Tooltip title="Settings">
              <Button
                variant="tertiary"
                size="sm"
                icon={<Settings className="h-8 w-8" />}
                onClick={() => setIsSettingsOpen(true)}
                className="!px-0 transition-colors hover:text-gray-700 hover:bg-gray-400/30 rounded-lg"
                aria-label="Settings"
              />
            </Tooltip>
          </div>
        </div>
      </div>

      <SignInModal
        isVisible={isEmailModalOpen}
        onClose={() => setIsEmailModalOpen(false)}
      />
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
      />
    </div>
  );
};

export default ContentHeader;
