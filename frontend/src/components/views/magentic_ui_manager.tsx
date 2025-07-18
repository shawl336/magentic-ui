import React from "react";
import { useTranslation } from "react-i18next";

export const MagenticUIManager: React.FC = () => {
  const { t, i18n } = useTranslation();
  return <div className="relative flex h-full w-full">{t("Magentic-UI  component")}</div>;
};

export default MagenticUIManager;