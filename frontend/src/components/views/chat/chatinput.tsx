import {
  PaperAirplaneIcon,
  ExclamationTriangleIcon,
  PauseCircleIcon,
  MicrophoneIcon,
  StopIcon,
} from "@heroicons/react/24/outline";
import * as React from "react";
import { appContext } from "../../../hooks/provider";
import { IStatus } from "../../types/app";
import {
  Upload,
  message,
  Button,
  Tooltip,
  notification,
  Modal,
  Dropdown,
  Menu,
} from "antd";
import type { UploadFile, UploadProps, RcFile } from "antd/es/upload/interface";
import {
  FileTextIcon,
  ImageIcon,
  XIcon,
  UploadIcon,
  PaperclipIcon,
} from "lucide-react";
import { InputRequest } from "../../types/datamodel";
import { debounce } from "lodash";
import { planAPI, settingsAPI } from "../api";
import RelevantPlans from "./relevant_plans";
import { IPlan } from "../../types/plan";
import PlanView from "./plan";
import { McpServerSelector } from "../../features/McpServerSelector/McpServerSelector";
import { MCPAgentConfig, MCPServerInfo } from "../../features/McpServersConfig/types";
import { extractMcpServers } from "../../features/McpServersConfig/McpServersList";
import { useTranslation } from "react-i18next";
import { getServerUrl } from "../../utils";

// Threshold for large text files (in characters)
const LARGE_TEXT_THRESHOLD = 1500;

interface ChatInputProps {
  onSubmit: (
    text: string,
    files: RcFile[],
    accepted?: boolean,
    plan?: IPlan
  ) => void;
  error: IStatus | null;
  disabled?: boolean;
  onCancel?: () => void;
  runStatus?: string;
  inputRequest?: InputRequest;
  isPlanMessage?: boolean;
  onPause?: () => void;
  enable_upload?: boolean;
  onExecutePlan?: (plan: IPlan) => void;
  onSubMenuChange: React.Dispatch<React.SetStateAction<string>>;
  mcpSelectorDisabled: boolean;
  selectedMcpServers: string[];
  onSelectedMcpServersChange: (servers: string[]) => void;
  runId?: number;
}

const ChatInput = React.forwardRef<{ focus: () => void }, ChatInputProps>(
  (
    {
      onSubmit,
      error,
      disabled = false,
      onCancel,
      runStatus,
      inputRequest,
      isPlanMessage = false,
      onPause,
      enable_upload = false,
      onExecutePlan,
      onSubMenuChange,
      mcpSelectorDisabled,
      selectedMcpServers,
      onSelectedMcpServersChange,
      runId
    },
    ref
  ) => {
    const { t, i18n } = useTranslation();
    const textAreaRef = React.useRef<HTMLTextAreaElement>(null);
    const textAreaDivRef = React.useRef<HTMLDivElement>(null);
    const [text, setText] = React.useState("");
    const [fileList, setFileList] = React.useState<UploadFile[]>([]);
    const [dragOver, setDragOver] = React.useState(false);
    const { darkMode, user } = React.useContext(appContext) as {
      darkMode: string;
      user: { email: string };
    };
    const [notificationApi, notificationContextHolder] =
      notification.useNotification();
    const [isSearching, setIsSearching] = React.useState(false);
    const [relevantPlans, setRelevantPlans] = React.useState<any[]>([]);
    const [allPlans, setAllPlans] = React.useState<any[]>([]);
    const [attachedPlan, setAttachedPlan] = React.useState<IPlan | null>(null);
    const [isLoading, setIsLoading] = React.useState(false);
    const userId = user?.email || "default_user";
    const [isRelevantPlansVisible, setIsRelevantPlansVisible] =
      React.useState(false);
    const [isPlanModalVisible, setIsPlanModalVisible] = React.useState(false);
    // 输入框高度设置：最低4行(约80px)，最高10行(约200px)
    const textAreaDefaultHeight = "100px"; // 3行
    const textAreaMaxHeight = "260px"; // 8行
    const isInputDisabled =
      disabled ||
      runStatus === "active" ||
      runStatus === "pausing" ||
      inputRequest?.input_type === "approval";
    const [mcpServers, setMcpServers] = React.useState<MCPServerInfo[]>([]);
    const [showScrollbar, setShowScrollbar] = React.useState(false);
    
    // 录音相关状态
    const [isRecording, setIsRecording] = React.useState(false);
    const [isRecordingModalVisible, setIsRecordingModalVisible] = React.useState(false);
    const [recordingTime, setRecordingTime] = React.useState(0);
    const [recognizedText, setRecognizedText] = React.useState(""); // 实时识别结果
    const recognizedTextRef = React.useRef<string>(""); // 用于在闭包中访问最新的识别结果
    const recordingTimerRef = React.useRef<number | null>(null);
    const streamRef = React.useRef<MediaStream | null>(null);
    const isCancelledRef = React.useRef<boolean>(false); // 标记是否取消录音
    const wsRef = React.useRef<WebSocket | null>(null); // WebSocket连接
    const audioContextRef = React.useRef<AudioContext | null>(null); // AudioContext
    const processorRef = React.useRef<ScriptProcessorNode | null>(null); // 音频处理器
    const sourceRef = React.useRef<MediaStreamAudioSourceNode | null>(null); // 音频源
    const silenceStartTimeRef = React.useRef<number | null>(null); // 静音开始时间
    const hasReceivedAudioRef = React.useRef<boolean>(false); // 是否已收到音频数据（用于判断是否开始录音）
    const finalResultReceivedRef = React.useRef<boolean>(false); // 是否已收到最终结果
    
    // Handle textarea auto-resize and scrollbar visibility
    React.useEffect(() => {
      if (textAreaRef.current) {
        // 先重置高度以获取准确的scrollHeight
        textAreaRef.current.style.height = textAreaDefaultHeight;
        const scrollHeight = textAreaRef.current.scrollHeight;
        const maxHeightPx = parseInt(textAreaMaxHeight);
        
        // 限制高度不超过最大高度（10行）
        const finalHeight = Math.min(scrollHeight, maxHeightPx);
        textAreaRef.current.style.height = `${finalHeight}px`;
        
        // 超过10行(约200px)后显示滚动条
        setShowScrollbar(scrollHeight > maxHeightPx);
      }
      if (textAreaDivRef.current) {
        textAreaDivRef.current.style.height = textAreaDefaultHeight;
        const scrollHeight = textAreaDivRef.current.scrollHeight;
        const maxHeightPx = parseInt(textAreaMaxHeight);
        const finalHeight = Math.min(scrollHeight, maxHeightPx);
        textAreaDivRef.current.style.height = `${finalHeight}px`;
      }
    }, [text, inputRequest, textAreaDefaultHeight, textAreaMaxHeight]);

    React.useEffect(() => {
      if (!error) {
        resetInput();
      }
    }, [error]);

    React.useEffect(() => {
      if (!isInputDisabled && textAreaRef.current) {
        textAreaRef.current.focus();
      }
    }, [isInputDisabled]);

    React.useEffect(() => {
      const fetchAllPlans = async () => {
        try {
          setIsLoading(true);

          const response = await planAPI.listPlans(userId);

          if (response) {
            if (Array.isArray(response)) {
              setAllPlans(response);
            } else {
              console.warn("Unexpected response format:", response);
            }
          } else {
            console.warn("Empty response received");
          }
        } catch (error) {
          console.error("Error fetching plans:", error);
        } finally {
          setIsLoading(false);
        }
      };
      const fetchMCPServers = async () => {
        if (!user?.email) {
          console.error("User not authenticated");
          setIsLoading(false);
          return;
        }

        try {
          setIsLoading(true);

          // Get user's latest settings from database
          const settings = await settingsAPI.getSettings(user.email);
          const mcpAgentConfigs: MCPAgentConfig[] = settings.mcp_agent_configs || [];
          const mcpServers = extractMcpServers(mcpAgentConfigs);
          setMcpServers(mcpServers);
        } catch (err) {
          console.error("Failed to fetch MCP servers:", err);
        } finally {
          setIsLoading(false);
        }
      };

      fetchMCPServers();
      fetchAllPlans();
    }, [userId, user?.email]);

    // Add paste event listener for images and large text
    const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
      if (isInputDisabled || !enable_upload) return;

      // Handle image paste
      if (e.clipboardData?.items) {
        let hasImageItem = false;

        for (let i = 0; i < e.clipboardData.items.length; i++) {
          const item = e.clipboardData.items[i];

          // Handle image items
          if (item.type.indexOf("image/") === 0) {
            hasImageItem = true;
            const file = item.getAsFile();

            if (file) {
              // Prevent the default paste behavior for images
              e.preventDefault();

              // Create a unique file name
              const fileName = `pasted-image-${new Date().getTime()}.png`;

              // Create a new File with a proper name
              const namedFile = new File([file], fileName, {
                type: file.type,
              });

              // Convert to the expected UploadFile format
              const uploadFile: UploadFile = {
                uid: `paste-${Date.now()}`,
                name: fileName,
                status: "done",
                size: namedFile.size,
                type: namedFile.type,
                originFileObj: namedFile as RcFile,
              };

              // Add to file list
              setFileList((prev) => [...prev, uploadFile]);

              // Show successful paste notification
              message.success(`File pasted successfully`);
            }
          }

          // Handle text items - only if there's a large amount of text
          if (item.type === "text/plain" && !hasImageItem) {
            item.getAsString((text) => {
              // Only process for large text
              if (text.length > LARGE_TEXT_THRESHOLD) {
                // We need to prevent the default paste behavior
                // But since we're in an async callback, we need to
                // manually clear the textarea's selection value
                setTimeout(() => {
                  if (textAreaRef.current) {
                    const currentValue = textAreaRef.current.value;
                    const selectionStart =
                      textAreaRef.current.selectionStart || 0;
                    const selectionEnd = textAreaRef.current.selectionEnd || 0;

                    // Remove the pasted text from the textarea
                    const newValue =
                      currentValue.substring(0, selectionStart - text.length) +
                      currentValue.substring(selectionEnd);

                    // Update the textarea
                    textAreaRef.current.value = newValue;
                    // Trigger the onChange event manually
                    setText(newValue);
                  }
                }, 0);

                // Prevent default paste for large text
                e.preventDefault();

                // Create a text file from the pasted content
                const blob = new Blob([text], { type: "text/plain" });
                const file = new File(
                  [blob],
                  `pasted-text-${new Date().getTime()}.txt`,
                  { type: "text/plain" }
                );

                // Add to file list
                const uploadFile: UploadFile = {
                  uid: `paste-${Date.now()}`,
                  name: file.name,
                  status: "done",
                  size: file.size,
                  type: file.type,
                  originFileObj: file as RcFile,
                };

                setFileList((prev) => [...prev, uploadFile]);

                // Notify user about the conversion
                notificationApi.info({
                  message: (
                    <span className="text-sm">
                      {t("Large Text Converted to File")}
                    </span>
                  ),
                  description: (
                    <span className="text-sm text-secondary">
                      {t("Your pasted text has been attached as a file.")}
                    </span>
                  ),
                  duration: 3,
                });
              }
            });
          }
        }
      }
    };

    const resetInput = () => {
      if (textAreaRef.current) {
        textAreaRef.current.value = "";
        textAreaRef.current.style.height = textAreaDefaultHeight;
        setText("");
        setFileList([]);
        setRelevantPlans([]);
        setAttachedPlan(null);
        setShowScrollbar(false);
      }
      if (textAreaDivRef.current) {
        textAreaDivRef.current.style.height = textAreaDefaultHeight;
      }
    };

    const searchableData = React.useMemo(() => {
      return allPlans.map((plan) => ({
        ...plan,
        taskLower: plan.task?.toLowerCase() || "",
        stepTexts:
          plan.steps?.map(
            (step: { title: string; details: string }) =>
              (step.title?.toLowerCase() || "") +
              " " +
              (step.details?.toLowerCase() || "")
          ) || [],
      }));
    }, [allPlans]);

    const searchPlans = React.useCallback(
      debounce((query: string) => {
        // Don't search if query is too short, no plans available, or plan is already attached
        if (
          query.length < 3 ||
          !searchableData ||
          searchableData.length === 0 ||
          attachedPlan
        ) {
          return;
        }

        setIsSearching(true);
        try {
          const searchTerms = query.toLowerCase().split(" ");
          const matchingPlans = searchableData.filter((plan) => {
            if (query.length <= 2) {
              if (plan.taskLower.startsWith(query.toLowerCase())) {
                return true;
              }
            }
            const taskMatches = searchTerms.every((term) =>
              plan.taskLower.includes(term)
            );
            if (taskMatches) {
              return true;
            }

            return plan.stepTexts.some((stepText: string | string[]) =>
              searchTerms.every((term) => stepText.includes(term))
            );
          });

          if (matchingPlans.length > 0) {
            const sortedPlans = matchingPlans.sort((a, b) => {
              return (
                new Date(b.created_at || "").getTime() -
                new Date(a.created_at || "").getTime()
              );
            });
            setRelevantPlans(sortedPlans.slice(0, 5));
            setIsRelevantPlansVisible(true);
          } else {
            setRelevantPlans([]);
            setAttachedPlan(null);
            setIsRelevantPlansVisible(false);
          }
        } catch (error) {
        } finally {
          setIsSearching(false);
        }
      }, 1000),
      [searchableData, runStatus, isPlanMessage, attachedPlan]
    );

    const handleTextChange = (
      event: React.ChangeEvent<HTMLTextAreaElement>
    ) => {
      const newText = event.target.value;
      setText(newText);

      // Clear relevant plans and attached plan as soon as the query changes
      setRelevantPlans([]);

      const shouldSearch = !(
        runStatus === "connected" || runStatus === "awaiting_input"
      );
      if (shouldSearch) {
        searchPlans(newText);
      } else if (relevantPlans.length > 0) {
        // Clear any relevant plans if not in the right state
        setRelevantPlans([]);
        setAttachedPlan(null);
      }
    };

    const submitInternal = (
      query: string,
      files: RcFile[],
      accepted: boolean,
      doResetInput: boolean = true
    ) => {
      if (attachedPlan) {
        onSubmit(query, files, accepted, attachedPlan);
      } else {
        onSubmit(query, files, accepted);
      }

      if (doResetInput) {
        resetInput();
      }
      textAreaRef.current?.focus();
    };

    // 将识别结果填入输入框的辅助函数
    const fillTextToInput = (text: string) => {
      if (!text || isCancelledRef.current) return;
      const currentText = textAreaRef.current?.value || '';
      const newText = currentText ? `${currentText}\n${text}` : text;
      setText(newText);
      if (textAreaRef.current) {
        textAreaRef.current.value = newText;
      }
      message.success(`语音转文字成功：${text}`);
    };

    // 清理录音资源（统一清理函数）
    const cleanupRecording = (closeWebSocket: boolean = true) => {
      setIsRecording(false);
      if (recordingTimerRef.current !== null) {
        window.clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
      }
      // 重置所有ref
      silenceStartTimeRef.current = null;
      hasReceivedAudioRef.current = false;
      finalResultReceivedRef.current = false;
      // 清理媒体流
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop());
        streamRef.current = null;
      }
      // 清理AudioContext相关资源
      if (processorRef.current) {
        processorRef.current.disconnect();
        processorRef.current = null;
      }
      if (sourceRef.current) {
        sourceRef.current.disconnect();
        sourceRef.current = null;
      }
      if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
        audioContextRef.current.close();
        audioContextRef.current = null;
      }
      // 关闭WebSocket连接
      if (closeWebSocket && wsRef.current) {
        try {
          if (wsRef.current.readyState === WebSocket.OPEN) {
            wsRef.current.close(1000, "Normal closure");
          }
        } catch (e) {
          console.error("Error closing WebSocket:", e);
        }
        wsRef.current = null;
      }
      setIsRecordingModalVisible(false);
      setRecognizedText("");
      recognizedTextRef.current = "";
    };

    // 开始录音（实时流式录音，参考demo逻辑）
    const startRecording = async () => {
      try {
        if (!runId) {
          message.error("无法获取会话ID，请刷新页面后重试");
          return;
        }
        
        // 请求麦克风权限
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        streamRef.current = stream;
        isCancelledRef.current = false;
        setRecognizedText("");
        recognizedTextRef.current = ""; // 重置ref
        finalResultReceivedRef.current = false; // 重置最终结果标记
        // 重置静音检测
        silenceStartTimeRef.current = null;
        hasReceivedAudioRef.current = false; // 重置音频接收标记
        
        // 创建AudioContext（16kHz采样率，参考demo要求）
        const targetSampleRate = 16000;
        const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)({
          sampleRate: targetSampleRate
        });
        audioContextRef.current = audioContext;
        
        // 创建音频源
        const source = audioContext.createMediaStreamSource(stream);
        sourceRef.current = source;
        
        // 创建ScriptProcessorNode用于实时处理音频数据
        // bufferSize: 4096, 输入通道数: 1, 输出通道数: 1
        const processor = audioContext.createScriptProcessor(4096, 1, 1);
        processorRef.current = processor;
        
        // 连接音频处理链
        source.connect(processor);
        processor.connect(audioContext.destination);
        
        // 建立WebSocket连接
        // WebSocket无法通过Gatsby代理，需要直接连接到后端端口
        const hostname = window.location.hostname;
        const protocol = window.location.protocol;
        const wsProtocol = protocol === 'https:' ? 'wss:' : 'ws:';
        
        // 获取后端端口（从环境变量或默认8081）
        let backendPort = "8081";
        // 在浏览器环境中，process.env可能不可用，需要从gatsby-config.ts的proxy配置推断
        // 或者直接使用默认端口8081
        if (typeof process !== 'undefined' && process.env && process.env.GATSBY_BACKEND_URL) {
          try {
            const backendUrl = new URL(process.env.GATSBY_BACKEND_URL);
            backendPort = backendUrl.port || "8081";
          } catch (e) {
            // 如果解析失败，使用默认端口
          }
        }
        
        // 始终直接连接到后端端口（WebSocket无法通过Gatsby代理）
        const wsUrl = `${wsProtocol}//${hostname}:${backendPort}/api/runs/${runId}/speech-to-text-stream`;
        
        console.log("=== WebSocket连接信息 ===");
        console.log("WebSocket URL:", wsUrl);
        console.log("后端端口:", backendPort);
        console.log("Run ID:", runId);
        console.log("当前页面URL:", window.location.href);
        console.log("========================");
        
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;
        
        // 音频处理相关变量
        let frameBuffer: Int16Array[] = [];
        const frameSize = 1280; // 每帧1280字节（参考demo）
        let wsReady = false; // WebSocket是否已准备好接收数据
        
        // WebSocket连接成功
        ws.onopen = () => {
          console.log("✓ WebSocket连接已建立");
          console.log("  URL:", wsUrl);
          console.log("  后端端口:", backendPort);
          console.log("  Run ID:", runId);
          setIsRecording(true);
          setIsRecordingModalVisible(true);
          setRecordingTime(0);
          wsReady = true; // 标记WebSocket已准备好
          console.log("[前端] WebSocket已准备好，开始接收音频数据");
          
          // 开始计时（最多60秒）
          const MAX_RECORDING_TIME = 60;
          recordingTimerRef.current = window.setInterval(() => {
            setRecordingTime(prev => {
              const newTime = prev + 1;
              if (newTime >= MAX_RECORDING_TIME) {
                stopRecording();
                message.warning(`录音时长已达上限（${MAX_RECORDING_TIME}秒），已自动停止`);
              }
              return newTime;
            });
          }, 1000);
        };
        
        // 接收识别结果
        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            
            if (data.type === "partial_result") {
              // 实时更新识别结果
              const text = data.text || "";
              setRecognizedText(text);
              recognizedTextRef.current = text;
            } else if (data.type === "final_result") {
              // 最终识别结果
              const finalText = data.text || "";
              recognizedTextRef.current = finalText;
              finalResultReceivedRef.current = true;
              if (finalText) {
                fillTextToInput(finalText);
              } else {
                message.warning("识别结果为空");
              }
              cleanupRecording(false);
            } else if (data.type === "error") {
              message.error(data.message || "语音识别失败");
              cleanupRecording();
            }
          } catch (e) {
            console.error("解析WebSocket消息失败:", e);
          }
        };
        
        ws.onerror = () => {
          // 错误由onclose统一处理
        };
        
        ws.onclose = (event) => {
          // 如果已经收到最终结果，直接清理资源
          if (finalResultReceivedRef.current) {
            cleanupRecording(false);
            return;
          }
          
          const isNormalClose = event.code === 1000 || event.code === 1001;
          
          if (isNormalClose) {
            // 正常关闭，等待最终结果（onclose可能在onmessage之前触发）
            let checkCount = 0;
            const maxChecks = 10;
            const checkInterval = 100;
            
            const checkForResult = () => {
              checkCount++;
              const hasResult = finalResultReceivedRef.current || recognizedTextRef.current.length > 0;
              
              if (hasResult) {
                const finalText = recognizedTextRef.current;
                if (finalText) {
                  fillTextToInput(finalText);
                }
                cleanupRecording(false);
              } else if (checkCount < maxChecks) {
                setTimeout(checkForResult, checkInterval);
              } else {
                // 超时，尝试使用部分识别结果
                const partialText = recognizedTextRef.current;
                if (partialText) {
                  fillTextToInput(partialText);
                }
                cleanupRecording(false);
              }
            };
            
            setTimeout(checkForResult, 50);
          } else {
            // 非正常关闭，立即清理资源
            cleanupRecording(false);
          }
          
          // 显示错误（仅在非正常关闭且未收到结果时）
          const hasResult = finalResultReceivedRef.current || recognizedTextRef.current.length > 0;
          if (!isNormalClose && !isCancelledRef.current && !hasResult) {
            let errorMsg = "语音识别服务连接失败";
            if (event.code === 1006) {
              errorMsg = "无法连接到语音识别服务，请检查后端服务是否运行";
            } else if (event.code === 1008) {
              errorMsg = `连接被拒绝: ${event.reason || "未知原因"}`;
            } else if (event.code === 1005 && !hasResult) {
              errorMsg = "连接已关闭，请重试";
            } else if (event.code !== 1005) {
              errorMsg = `连接失败 (错误码: ${event.code}${event.reason ? `, 原因: ${event.reason}` : ''})`;
            }
            if (errorMsg) {
              message.error(errorMsg);
            }
          }
        };
        
        // 处理音频数据（实时转换为PCM并发送）
        let frameSendCount = 0; // 记录发送的帧数
        const SILENCE_THRESHOLD = 0.01; // 静音阈值（可根据实际情况调整）
        const SILENCE_DURATION = 3000; // 静音持续时间（毫秒），超过此时间自动停止
        
        processor.onaudioprocess = (e) => {
          // 只有在WebSocket已准备好且未取消时才处理
          if (!wsReady || !ws || ws.readyState !== WebSocket.OPEN || isCancelledRef.current) {
            return;
          }
          
          // 获取音频数据（Float32Array，范围-1到1）
          const inputData = e.inputBuffer.getChannelData(0);
          
          // 计算音频能量（RMS - Root Mean Square）
          let sum = 0;
          for (let i = 0; i < inputData.length; i++) {
            sum += inputData[i] * inputData[i];
          }
          const rms = Math.sqrt(sum / inputData.length);
          const volume = rms; // 音量值（0-1之间）
          
          // 检测是否有声音（用于判断是否开始录音）
          if (volume >= SILENCE_THRESHOLD) {
            hasReceivedAudioRef.current = true;
          }
          
          // 只有在已经收到音频数据后才进行静音检测
          if (!hasReceivedAudioRef.current) {
            return;
          }
          
          // 检测静音
          const isSilent = volume < SILENCE_THRESHOLD;
          const now = Date.now();
          
          if (isSilent) {
            // 检测到静音，开始或继续计时
            if (silenceStartTimeRef.current === null) {
              silenceStartTimeRef.current = now;
            } else if (now - silenceStartTimeRef.current >= SILENCE_DURATION) {
              // 静音超过3秒，自动停止录音
              silenceStartTimeRef.current = null;
              stopRecording();
              return;
            }
          } else {
            // 检测到有声音，重置静音计时
            silenceStartTimeRef.current = null;
          }
          
          // 转换为16-bit PCM（Int16Array）
          const pcmData = new Int16Array(inputData.length);
          for (let i = 0; i < inputData.length; i++) {
            const s = Math.max(-1, Math.min(1, inputData[i]));
            pcmData[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
          }
          
          // 将PCM数据添加到缓冲区
          frameBuffer.push(pcmData);
          
          // 计算缓冲区总字节数（每个样本2字节）
          let totalBytes = 0;
          for (const frame of frameBuffer) {
            totalBytes += frame.length * 2;
          }
          
          // 当缓冲区达到或超过一帧大小时发送
          while (totalBytes >= frameSize) {
            // 合并缓冲区中的数据，取正好一帧的大小
            const samplesPerFrame = frameSize / 2; // 每帧的样本数
            const combined = new Int16Array(samplesPerFrame);
            let offset = 0;
            let remainingSamples = samplesPerFrame;
            
            // 从缓冲区中提取一帧的数据
            const newBuffer: Int16Array[] = [];
            for (let i = 0; i < frameBuffer.length && remainingSamples > 0; i++) {
              const frame = frameBuffer[i];
              const takeSamples = Math.min(remainingSamples, frame.length);
              
              combined.set(frame.subarray(0, takeSamples), offset);
              offset += takeSamples;
              remainingSamples -= takeSamples;
              
              // 如果还有剩余数据，保留在缓冲区
              if (takeSamples < frame.length) {
                newBuffer.push(frame.subarray(takeSamples));
              }
            }
            
            // 更新缓冲区
            frameBuffer = newBuffer;
            totalBytes -= frameSize;
            
            // 发送一帧数据（正好1280字节）
            const frameToSend = new Uint8Array(combined.buffer);
            
            try {
              frameSendCount++;
              if (frameSendCount === 1 || frameSendCount % 50 === 0) {
                console.log(`[前端] 发送第 ${frameSendCount} 帧音频数据 (${frameToSend.length} 字节)`);
              }
              ws.send(frameToSend);
            } catch (err) {
              console.error("发送音频数据失败:", err);
              return; // 发送失败时停止处理
            }
          }
        };
        
      } catch (error) {
        console.error("Error starting recording:", error);
        message.error("无法访问麦克风，请检查权限设置");
        cleanupRecording();
      }
    };
    
    // 停止录音
    const stopRecording = () => {
      if (!isRecording) {
        return;
      }
      
      setIsRecording(false);
      isCancelledRef.current = false;
      
      // 清除计时器
      if (recordingTimerRef.current !== null) {
        window.clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
      }
      
      // 重置静音检测
      silenceStartTimeRef.current = null;
      hasReceivedAudioRef.current = false;
      finalResultReceivedRef.current = false;
      
      // 停止音频处理（防止继续发送数据）
      if (processorRef.current) {
        processorRef.current.disconnect();
        processorRef.current = null;
      }
      if (sourceRef.current) {
        sourceRef.current.disconnect();
        sourceRef.current = null;
      }
      if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
        audioContextRef.current.close();
        audioContextRef.current = null;
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop());
        streamRef.current = null;
      }
      
      // 发送停止信号到WebSocket
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        try {
          wsRef.current.send(JSON.stringify({ type: "stop" }));
        } catch (error) {
          console.error("发送停止信号失败:", error);
          cleanupRecording();
        }
      } else {
        cleanupRecording();
      }
    };
    
    // 取消录音
    const cancelRecording = () => {
      if (!isRecording) {
        return;
      }
      
      setIsRecording(false);
      isCancelledRef.current = true;
      
      // 清除计时器
      if (recordingTimerRef.current !== null) {
        window.clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
      }
      
      // 发送取消信号到WebSocket
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        try {
          wsRef.current.send(JSON.stringify({ type: "cancel" }));
        } catch (error) {
          console.error("Error sending cancel signal:", error);
        }
      }
      
      // 清理资源
      cleanupRecording();
      setRecordingTime(0);
      setRecognizedText("");
      recognizedTextRef.current = ""; // 重置ref
      finalResultReceivedRef.current = false; // 重置最终结果标记
      hasReceivedAudioRef.current = false; // 重置音频接收标记
    };
    
    // 注意：convertWebmToPcm 和 handleRecordingComplete 函数已不再需要
    // 因为现在使用实时流式识别，直接通过AudioContext获取PCM数据并发送
    
    // 格式化录音时间
    const formatRecordingTime = (seconds: number): string => {
      const mins = Math.floor(seconds / 60);
      const secs = seconds % 60;
      return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    };
    
    // 组件卸载时清理录音资源
    React.useEffect(() => {
      return () => {
        cleanupRecording();
      };
    }, []);
    
    const handleSubmit = () => {
      if (
        (textAreaRef.current?.value || fileList.length > 0) &&
        !isInputDisabled
      ) {
        const query = textAreaRef.current?.value || "";

        // Get all valid RcFile objects
        const files = fileList
          .filter((file) => file.originFileObj)
          .map((file) => file.originFileObj as RcFile);

        submitInternal(query, files, false);
      }
    };

    const handlePause = () => {
      if (onPause) {
        onPause();
      }
    };

    const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        handleSubmit();
      }
    };

    // Expose focus method via ref
    React.useImperativeHandle(ref, () => ({
      focus: () => {
        textAreaRef.current?.focus();
      },
    }));

    // Add helper function for file addition
    const handleFileAdd = (file: File): boolean => {
      // Add file to fileList
      const uploadFile: UploadFile = {
        uid: `file-${Date.now()}-${file.name}`,
        name: file.name,
        status: "done",
        size: file.size,
        type: file.type,
        originFileObj: file as RcFile,
      };

      setFileList((prev) => [...prev, uploadFile]);
      return true;
    };

    // Update the upload props to use the new helper function
    const uploadProps: UploadProps = {
      name: "file",
      multiple: true,
      fileList,
      beforeUpload: (file: RcFile) => {
        handleFileAdd(file);
        return false; // Prevent automatic upload
      },
      onRemove: (file: UploadFile) => {
        setFileList(fileList.filter((item) => item.uid !== file.uid));
      },
      showUploadList: false, // We'll handle our own custom file preview
      customRequest: (options: any) => {
        // Mock successful upload since we're not actually uploading anywhere yet
        if (options.onSuccess) {
          options.onSuccess("ok", options.file);
        }
      },
    };

    const getFileIcon = (file: UploadFile) => {
      const fileType = file.type || "";
      const fileName = file.name || "";

      if (fileType.startsWith("image/")) {
        return <ImageIcon className="w-4 h-4" />;
      }

      // Check for specific file types based on extension
      const extension = fileName.split(".").pop()?.toLowerCase();
      switch (extension) {
        case "pdf":
          return <FileTextIcon className="w-4 h-4 text-red-500" />;
        case "doc":
        case "docx":
          return <FileTextIcon className="w-4 h-4 text-blue-500" />;
        case "xls":
        case "xlsx":
          return <FileTextIcon className="w-4 h-4 text-green-500" />;
        case "zip":
        case "rar":
        case "7z":
          return <FileTextIcon className="w-4 h-4 text-yellow-500" />;
        default:
          return <FileTextIcon className="w-4 h-4" />;
      }
    };

    // Add drag and drop handlers
    const handleDragOver = (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!isInputDisabled && enable_upload) {
        setDragOver(true);
      }
    };

    const handleDragLeave = (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);
    };

    // Update the drop handler to use the new helper function
    const handleDrop = async (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);

      if (isInputDisabled || !enable_upload) return;

      const droppedFiles = Array.from(e.dataTransfer.files);
      droppedFiles.forEach(handleFileAdd);
    };

    const handleUsePlan = (plan: IPlan) => {
      setRelevantPlans([]); // Close the dropdown
      setAttachedPlan(plan);
    };

    const handlePlanClick = () => {
      setIsPlanModalVisible(true);
    };

    const handlePlanModalClose = () => {
      setIsPlanModalVisible(false);
    };

    React.useEffect(() => {
      const handleClickOutside = (e: MouseEvent) => {
        // Only process if dropdown is visible
        if (!isRelevantPlansVisible) return;

        // Get the clicked element
        const target = e.target as Node;

        // Check if click was on textarea or within the plans dropdown
        const textAreaElement = textAreaRef.current;
        const planElement = document.querySelector(
          '[data-component="relevant-plans"]'
        );

        const isClickInsideTextArea =
          textAreaElement && textAreaElement.contains(target);
        const isClickInsidePlans = planElement && planElement.contains(target);

        // Hide dropdown if click is outside both elements
        if (!isClickInsideTextArea && !isClickInsidePlans) {
          setIsRelevantPlansVisible(false);
        }
      };

      // Handle escape key
      const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === "Escape" && isRelevantPlansVisible) {
          setIsRelevantPlansVisible(false);
        }
      };

      // Add listeners
      document.addEventListener("click", handleClickOutside);
      document.addEventListener("keydown", handleKeyDown);

      return () => {
        document.removeEventListener("click", handleClickOutside);
        document.removeEventListener("keydown", handleKeyDown);
      };
    }, [isRelevantPlansVisible]);

    const goToMcpServersTab = React.useCallback(() => {
      onSubMenuChange("mcp_servers");
    }, [onSubMenuChange]);

    const handleMcpServerSelectionChange = React.useCallback((newSelection: string[]) => {
      onSelectedMcpServersChange(newSelection);
    }, [onSelectedMcpServersChange]);


    return (
      <div className="mt-2 w-full relative">
        {notificationContextHolder}

        {/* Relevant Plans Indicator and Dropdown */}
        {isRelevantPlansVisible && (
          <RelevantPlans
            isSearching={isSearching}
            relevantPlans={relevantPlans}
            darkMode={darkMode}
            onUsePlan={handleUsePlan}
          />
        )}

        {/* Selected MCP Tools Display */}
        {selectedMcpServers.length > 0 && (
          <div
            className={`-mb-2 mx-1 ${darkMode === "dark" ? "bg-[#333333]" : "bg-gray-100"
              } rounded-t border-b-0 p-2 flex border flex-wrap gap-2`}
          >
            {selectedMcpServers.map((serverName) => (
              <div
                key={serverName}
                className={`flex items-center gap-1 ${darkMode === "dark"
                  ? "bg-[#444444] text-white"
                  : "bg-white text-black"
                  } rounded px-2 py-1 text-xs`}
              >
                <span className="truncate max-w-[150px]">🔧 {serverName}</span>
                {runStatus === "created" && (
                  <Button
                    type="text"
                    size="small"
                    className="p-0 ml-1 flex items-center justify-center"
                    onClick={() =>
                      onSelectedMcpServersChange(
                        selectedMcpServers.filter((s) => s !== serverName)
                      )
                    }
                    icon={<XIcon className="w-3 h-3" />}
                  />
                )}
              </div>
            ))}
          </div>
        )}

        {/* Attached Items Preview */}
        {(attachedPlan || fileList.length > 0) && (
          <div
            className={`${selectedMcpServers.length > 0 ? '-mt-2' : '-mb-2'} mx-1 ${darkMode === "dark" ? "bg-[#333333]" : "bg-gray-100"
              } ${selectedMcpServers.length > 0 ? 'rounded-b border-t-0' : 'rounded-t border-b-0'} p-2 flex border flex-wrap gap-2`}
          >
            {/* Attached Plan */}
            {attachedPlan && (
              <div
                className={`flex items-center gap-1 ${darkMode === "dark"
                  ? "bg-[#444444] text-white"
                  : "bg-white text-black"
                  } rounded px-2 py-1 text-xs cursor-pointer hover:opacity-80 transition-opacity`}
                onClick={handlePlanClick}
              >
                <span className="truncate max-w-[150px]">
                  📋 {attachedPlan.task}
                </span>
                <Button
                  type="text"
                  size="small"
                  className="p-0 ml-1 flex items-center justify-center"
                  onClick={(e: { stopPropagation: () => void }) => {
                    e.stopPropagation();
                    setAttachedPlan(null);
                  }}
                  icon={<XIcon className="w-3 h-3" />}
                />
              </div>
            )}

            {/* Attached Files */}
            {fileList.map((file) => (
              <div
                key={file.uid}
                className={`flex items-center gap-1 ${darkMode === "dark"
                  ? "bg-[#444444] text-white"
                  : "bg-white text-black"
                  } rounded px-2 py-1 text-xs`}
              >
                {getFileIcon(file)}
                <span className="truncate max-w-[150px]">{file.name}</span>
                <Button
                  type="text"
                  size="small"
                  className="p-0 ml-1 flex items-center justify-center"
                  onClick={() =>
                    setFileList((prev) =>
                      prev.filter((f) => f.uid !== file.uid)
                    )
                  }
                  icon={<XIcon className="w-3 h-3" />}
                />
              </div>
            ))}
          </div>
        )}

        {/* Plan View Modal */}
        <Modal
          title={`Plan: ${attachedPlan?.task || "Untitled Plan"}`}
          open={isPlanModalVisible}
          onCancel={handlePlanModalClose}
          footer={null}
          width={800}
          destroyOnClose
        >
          {attachedPlan && (
            <PlanView
              task={attachedPlan.task || ""}
              plan={attachedPlan.steps || []}
              viewOnly={true}
              setPlan={() => { }}
            />
          )}
        </Modal>

        {/* 录音弹窗 */}
        <Modal
          title={isRecording ? t("正在录音") : t("录音")}
          open={isRecordingModalVisible}
          onCancel={isRecording ? cancelRecording : undefined}
          footer={isRecording ? [
            <Button key="cancel" onClick={cancelRecording}>
              {t("取消")}
            </Button>,
            <Button
              key="stop"
              type="primary"
              danger
              onClick={stopRecording}
              disabled={!isRecording}
            >
              {t("停止录音")}
            </Button>,
          ] : null}
          closable={!isRecording}
          maskClosable={false}
        >
          <div className="flex flex-col items-center justify-center py-8">
            {isRecording ? (
              <>
                <div className="mb-4">
                  <MicrophoneIcon className="h-16 w-16 text-red-500 animate-pulse" />
                </div>
                <div className="text-2xl font-bold mb-2">
                  {formatRecordingTime(recordingTime)}
                </div>
                <div className="text-gray-500 text-sm mb-4">
                  {t("正在录音中，请说话...")}
                </div>
                {recognizedText && (
                  <div className="w-full max-w-md mt-4 p-4 bg-gray-100 dark:bg-gray-800 rounded-lg">
                    <div className="text-sm text-gray-600 dark:text-gray-400 mb-2">
                      {t("实时识别结果：")}
                    </div>
                    <div className="text-base text-gray-800 dark:text-gray-200">
                      {recognizedText}
                    </div>
                  </div>
                )}
              </>
            ) : null}
          </div>
        </Modal>

        <div className="mt-2 rounded shadow-sm flex">
          <div
            className={`flex w-full ${dragOver ? "opacity-50" : ""}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <div className="flex w-full">
              <div className="flex-1">
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    handleSubmit();
                  }}
                >
                  <textarea
                    id="queryInput"
                    name="queryInput"
                    onPaste={handlePaste}
                    ref={textAreaRef}
                    defaultValue={""}
                    onChange={handleTextChange}
                    onKeyDown={handleKeyDown}
                    className={`flex items-center w-full resize-none border-l border-t border-b border-accent p-2 pl-5 rounded-l-lg ${darkMode === "dark"
                      ? "bg-[#444444] text-white"
                      : "bg-white text-black"
                      } ${isInputDisabled ? "cursor-not-allowed" : ""
                      } focus:outline-none ${showScrollbar ? "scroll" : ""}`}
                    style={{
                      maxHeight: textAreaMaxHeight,
                      overflowY: showScrollbar ? "auto" : "hidden",
                      minHeight: textAreaDefaultHeight,
                    }}
                    placeholder={
                      runStatus === "awaiting_input"
                        ? t("Type your response here and let Agents know of any changes in the browser.")
                        : enable_upload
                          ? dragOver
                            ? t("Drop files here...")
                            : t("Type your message here...")
                          : t("Type your message here...")
                    }
                    disabled={isInputDisabled}
                  />
                </form>
              </div>

              <div
                className={`flex items-center justify-center gap-2 border-t border-r border-b border-accent px-2 rounded-r-lg ${darkMode === "dark"
                  ? "bg-[#444444] text-white"
                  : "bg-white text-black"
                  }`}
              >

                {runStatus === "created" && (
                  <McpServerSelector
                    servers={mcpServers}
                    onAddMcpServer={goToMcpServersTab}
                    runStatus={runStatus}
                    value={selectedMcpServers}
                    onChange={handleMcpServerSelectionChange}
                  />
                )}

                {/* File upload button replaced with Dropdown */}
                {enable_upload && (
                  <div
                    className={`${isInputDisabled ? "pointer-events-none opacity-50" : ""
                      }`}
                  >
                    <Dropdown
                      overlay={
                        <Menu>
                          <Menu.Item
                            key="attach-file"
                            className="!py-0 !my-0 !h-8"
                          >
                            <Upload {...uploadProps} showUploadList={false}>
                              <span className="flex items-center gap-2">
                                <PaperclipIcon className="w-4 h-4" />
                                {t("Attach File")}
                              </span>
                            </Upload>
                          </Menu.Item>
                          <Menu.Divider />
                          <Menu.SubMenu key="attach-plan" title="Attach Plan">
                            {allPlans.length === 0 ? (
                              <Menu.Item disabled key="no-plans">
                                {t("No plans available")}
                              </Menu.Item>
                            ) : (
                              allPlans.map((plan: any) => (
                                <Menu.Item
                                  key={plan.id || plan.task}
                                  onClick={() => handleUsePlan(plan)}
                                >
                                  {plan.task}
                                </Menu.Item>
                              ))
                            )}
                          </Menu.SubMenu>
                        </Menu>
                      }
                      trigger={["click"]}
                    >
                      <Tooltip
                        title={
                          <span className="text-sm">{t("Attach File or Plan")}</span>
                        }
                        placement="top"
                      >
                        <button
                          type="button"
                          disabled={isInputDisabled}
                          className="flex justify-center items-center transition duration-300"
                        >
                          <UploadIcon className="h-6 -mt-1 w-6 text-accent" />
                        </button>
                      </Tooltip>
                    </Dropdown>
                  </div>
                )}

                {/* 录音按钮 */}
                {enable_upload && !isInputDisabled && (
                  <Tooltip
                    title={<span className="text-sm">{t("语音转文字")}</span>}
                    placement="top"
                  >
                    <button
                      type="button"
                      onClick={startRecording}
                      disabled={isInputDisabled || isRecording}
                      className={`flex justify-center items-center transition duration-300 ${isInputDisabled || isRecording
                        ? "cursor-not-allowed opacity-50"
                        : "hover:opacity-80"
                        }`}
                    >
                      <MicrophoneIcon className="h-6 w-6 text-accent" />
                    </button>
                  </Tooltip>
                )}

                {runStatus === "active" && (
                  <button
                    type="button"
                    onClick={handlePause}
                    className="bg-magenta-800 hover:bg-magenta-900 text-white rounded flex justify-center items-center w-11 h-9 transition duration-300"
                  >
                    <PauseCircleIcon className="h-6 w-6" />
                  </button>
                )}
                {
                  <button
                    type="button"
                    onClick={handleSubmit}
                    disabled={isInputDisabled}
                    className={`bg-magenta-800 transition duration-300 rounded flex justify-center items-center w-11 h-9 ${isInputDisabled
                      ? "cursor-not-allowed"
                      : "hover:bg-magenta-900"
                      }`}
                  >
                    <PaperAirplaneIcon className="h-6 w-6 text-white" />
                  </button>
                }
              </div>
            </div>
          </div>
        </div>

        {error && !error.status && (
          <div className="p-2 border rounded mt-4 text-orange-500 text-sm">
            <ExclamationTriangleIcon className="h-5 text-orange-500 inline-block mr-2" />
            {error.message}
          </div>
        )}
      </div>
    );
  }
);

export default ChatInput;