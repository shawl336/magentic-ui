import React from "react";
import { ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

interface SecurityBannerProps {
  className?: string;
  style?: React.CSSProperties;
}

const SecurityBanner: React.FC<SecurityBannerProps> = ({
  className = "",
  style = {},
}) => {
  const { t, i18n } = useTranslation();
  return (
    <div
      className={`bg-yellow-100 border-b border-yellow-300 text-yellow-800 px-4 py-3 flex items-center ${className}`}
      style={style}
    >
      <ShieldAlert className="h-5 w-5 mr-2 flex-shrink-0" />
      <p className="text-sm">
        <span className="font-bold">{t("Security Note:")}</span> {t("Agents cannot see what \n you do when you take control. Be cautious about entering passwords or \n sensitive information.")}
      </p>
    </div>
  );
};

export default SecurityBanner;
