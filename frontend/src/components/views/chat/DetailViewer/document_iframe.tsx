import React, { useEffect, useRef, useContext } from "react";
import { useTranslation } from "react-i18next";
import { appContext } from "../../../../hooks/provider";

interface DocumentIframeProps {
  docUrl?: string;
  style?: React.CSSProperties;
  className?: string;
}

const DocumentIframe: React.FC<DocumentIframeProps> = ({
  docUrl,
  style,
  className,
}) => {
  const { t } = useTranslation();
  const { user } = useContext(appContext);
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = React.useState<boolean>(false);
  const [error, setError] = React.useState<string | null>(null);
  const getServerHost = () => {
          // 优先使用当前页面的主机名，这样支持不同部署环境
          const currentHost = window.location.hostname;
          // 如果是localhost或127.0.0.1，使用Docker bridge IP
          if (currentHost === 'localhost' || currentHost === '127.0.0.1') {
            return '172.17.0.1';
          }
          // 否则使用当前主机名
          return currentHost;
        };
  const IP_ADDRESS = getServerHost();

  useEffect(() => {
    if (!docUrl || !containerRef.current) return;

    setLoading(true);
    setError(null);

    // 转换URL为OnlyOffice可访问的格式
    let apiPath = docUrl;
    if (docUrl.startsWith('/files/')) {
      apiPath = docUrl.substring('/files/'.length);
    } else if (docUrl.startsWith('files/')) {
      apiPath = docUrl.substring('files/'.length);
    } else if (docUrl.startsWith('/') && !docUrl.startsWith('//')) {
      apiPath = docUrl.startsWith('/') ? docUrl.substring(1) : docUrl;
    }

    // 通过前端proxy提供文档 - 使用宿主机IP，这样OnlyOffice容器能访问
    // 添加时间戳参数来破坏缓存，确保获取最新版本
    const timestamp = Date.now();
    const documentUrl = `http://${IP_ADDRESS}:8081/api/document/${apiPath}?t=${timestamp}`;
    const documentKey = `doc_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

    // 动态加载OnlyOffice API
    const loadOnlyOfficeAPI = () => {
      return new Promise<void>((resolve, reject) => {
        if ((window as any).DocsAPI) {
          resolve();
          return;
        }

        const script = document.createElement('script');
        script.src = `http://${IP_ADDRESS}:18099/web-apps/apps/api/documents/api.js`;
        script.onload = () => resolve();
        script.onerror = () => reject(new Error('Failed to load OnlyOffice API'));
        document.head.appendChild(script);
      });
    };

    // 初始化OnlyOffice编辑器
    const initEditor = async () => {
      try {
        await loadOnlyOfficeAPI();

        const config = {
          type: 'desktop',
          document: {
            fileType: 'docx',
            key: documentKey,
            title: 'Document',
            url: documentUrl,
          },
          documentType: 'word',
          editorConfig: {
            mode: 'edit',
            lang: 'zh-CN',
            callbackUrl: `http://${IP_ADDRESS}:8081/api/callback?filepath=${encodeURIComponent(apiPath)}`,
            user: {
              id: user?.email || '游客',
              name: user?.email?.split('@')[0] || '游客',
            },
            customization: {
              forcesave: true, // 启用强制保存，确保修改及时同步
              autosave: true, // 启用自动保存
              // 添加保存提示，让用户知道修改会被保存
              showHeader: true,
              showFooter: true,
            },
            // 配置保存行为
            coEditing: {
              mode: "fast", // 快速协作模式
              change: true, // 允许实时变更
            },
          },
          height: '100%',
          width: '100%',
        };

        if ((window as any).DocsAPI && containerRef.current) {
          // 清除容器内容
          containerRef.current.innerHTML = '';

          // 创建编辑器
          const docEditor = new (window as any).DocsAPI.DocEditor('onlyoffice-editor', config);
          console.log('OnlyOffice editor initialized:', docEditor);
          setLoading(false);
        }
      } catch (err) {
        console.error('Failed to initialize OnlyOffice editor:', err);
        setError('Failed to load document viewer');
        setLoading(false);
      }
    };

    initEditor();
  }, [docUrl, t]);

  if (!docUrl) {
    return (
      <div className="flex-1 w-full h-full min-h-0 flex items-center justify-center">
        <p>{t("Waiting for document to load...")}</p>
      </div>
    );
  }

  return (
    <div className={`flex-1 w-full h-full flex flex-col ${className || ''}`} style={style}>
      {loading && (
        <div className="w-full py-2 text-center text-gray-500">
          {t("Loading document...")}
        </div>
      )}
      {error && (
        <div className="w-full py-2 text-center text-red-600">
          {t("Failed to display document")}: {error}
        </div>
      )}
      <div
        id="onlyoffice-editor"
        ref={containerRef}
        className="flex-1 w-full h-full bg-white"
        style={{ minHeight: "400px" }}
      />
    </div>
  );
};

export default DocumentIframe;
