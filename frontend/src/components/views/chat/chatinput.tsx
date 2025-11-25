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
    const mediaRecorderRef = React.useRef<MediaRecorder | null>(null);
    const audioChunksRef = React.useRef<Blob[]>([]);
    const recordingTimerRef = React.useRef<number | null>(null);
    const [isConverting, setIsConverting] = React.useState(false);
    const streamRef = React.useRef<MediaStream | null>(null);
    const isCancelledRef = React.useRef<boolean>(false); // 标记是否取消录音
    
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

    // 清理录音资源（统一清理函数）
    const cleanupRecording = () => {
      setIsRecording(false);
      if (recordingTimerRef.current !== null) {
        window.clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop());
        streamRef.current = null;
      }
      setIsRecordingModalVisible(false);
    };

    // 开始录音（参考demo逻辑：简单直接的录音流程）
    const startRecording = async () => {
      try {
        // 请求麦克风权限
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        streamRef.current = stream;
        
        // 创建 MediaRecorder，优先使用opus编码（质量更好）
        const options: MediaRecorderOptions = {};
        if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
          options.mimeType = 'audio/webm;codecs=opus';
        } else if (MediaRecorder.isTypeSupported('audio/webm')) {
          options.mimeType = 'audio/webm';
        } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
          options.mimeType = 'audio/mp4';
        }
        
        const recorder = new MediaRecorder(stream, Object.keys(options).length > 0 ? options : undefined);
        mediaRecorderRef.current = recorder;
        audioChunksRef.current = [];
        isCancelledRef.current = false;
        
        // 收集音频数据
        recorder.ondataavailable = (event) => {
          if (event.data && event.data.size > 0) {
            audioChunksRef.current.push(event.data);
          }
        };
        
        // 录音停止时的处理
        recorder.onstop = () => {
          // 停止媒体流
          if (streamRef.current) {
            streamRef.current.getTracks().forEach(track => track.stop());
            streamRef.current = null;
          }
          
          // 如果未取消，处理录音完成
          if (!isCancelledRef.current) {
            handleRecordingComplete();
          } else {
            // 取消时清理状态
            setIsRecordingModalVisible(false);
            audioChunksRef.current = [];
            setRecordingTime(0);
          }
        };
        
        // 错误处理
        recorder.onerror = (event: any) => {
          console.error("MediaRecorder error:", event);
          message.error("录音过程中发生错误");
          cleanupRecording();
        };
        
        // 开始录音（每1秒收集一次数据）
        recorder.start(1000);
        
        // 更新状态
        setIsRecording(true);
        setIsRecordingModalVisible(true);
        setRecordingTime(0);
        
        // 开始计时（最多60秒）
        const MAX_RECORDING_TIME = 60;
        recordingTimerRef.current = window.setInterval(() => {
          setRecordingTime(prev => {
            const newTime = prev + 1;
            // 达到最大时长自动停止
            if (newTime >= MAX_RECORDING_TIME) {
              stopRecording();
              message.warning(`录音时长已达上限（${MAX_RECORDING_TIME}秒），已自动停止`);
            }
            return newTime;
          });
        }, 1000);
        
      } catch (error) {
        console.error("Error starting recording:", error);
        message.error("无法访问麦克风，请检查权限设置");
        cleanupRecording();
      }
    };
    
    // 停止录音
    const stopRecording = () => {
      if (!mediaRecorderRef.current || !isRecording) {
        return;
      }
      
      if (mediaRecorderRef.current.state === 'inactive') {
        return;
      }
      
      setIsRecording(false);
      isCancelledRef.current = false;
      
      // 清除计时器
      if (recordingTimerRef.current !== null) {
        window.clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
      }
      
      // 停止录音（会在onstop回调中处理）
      try {
        mediaRecorderRef.current.stop();
      } catch (error) {
        console.error("Error stopping recorder:", error);
        cleanupRecording();
      }
    };
    
    // 取消录音
    const cancelRecording = () => {
      if (!mediaRecorderRef.current || !isRecording) {
        return;
      }
      
      setIsRecording(false);
      isCancelledRef.current = true;
      
      // 清除计时器和数据
      if (recordingTimerRef.current !== null) {
        window.clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
      }
      audioChunksRef.current = [];
      
      // 停止录音
      if (mediaRecorderRef.current.state !== 'inactive') {
        try {
          mediaRecorderRef.current.stop();
        } catch (error) {
          console.error("Error stopping recorder:", error);
        }
      }
      
      // 清理资源
      cleanupRecording();
      setRecordingTime(0);
      setIsConverting(false);
    };
    
    // 处理录音完成（参考demo逻辑：录音 -> 转换 -> 发送）
    const handleRecordingComplete = async () => {
      // 如果已取消，不处理
      if (isCancelledRef.current) {
        return;
      }
      
      // 检查录音数据
      if (audioChunksRef.current.length === 0) {
        message.warning("录音数据为空，请重新录音");
        setIsRecordingModalVisible(false);
        return;
      }
      
      if (!runId) {
        message.error("无法获取会话ID，请刷新页面后重试");
        setIsRecordingModalVisible(false);
        return;
      }
      
      try {
        setIsConverting(true);
        
        // 合并音频数据
        const mimeType = mediaRecorderRef.current?.mimeType || 'audio/webm';
        const audioBlob = new Blob(audioChunksRef.current, { type: mimeType });
        
        // 转换为PCM格式（16kHz，参考demo要求）
        const pcmBlob = await convertWebmToPcm(audioBlob);
        
        // 创建FormData并发送到后端
        const formData = new FormData();
        const audioFile = new File([pcmBlob], `recording_${Date.now()}.pcm`, {
          type: 'audio/pcm'
        });
        formData.append('audio_file', audioFile);
        
        // 调用后端API
        const serverUrl = getServerUrl();
        const response = await fetch(`${serverUrl}/runs/${runId}/speech-to-text`, {
          method: 'POST',
          body: formData,
        });
        
        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          const errorMsg = errorData.detail || errorData.message || `HTTP ${response.status}: 语音转文字失败`;
          throw new Error(errorMsg);
        }
        
        const result = await response.json();
        
        if (result.status && result.text) {
          // 检查识别结果是否为空
          const recognizedText = result.text.trim();
          if (!recognizedText) {
            throw new Error('识别结果为空，请重新录音');
          }
          
          // 将转换后的文字填入输入框
          const currentText = textAreaRef.current?.value || '';
          const newText = currentText ? `${currentText}\n${recognizedText}` : recognizedText;
          setText(newText);
          if (textAreaRef.current) {
            textAreaRef.current.value = newText;
          }
          message.success(`语音转文字成功：${recognizedText}`);
        } else {
          throw new Error(result.message || '语音转文字失败：未返回识别结果');
        }
        
      } catch (error: any) {
        console.error("Error converting speech to text:", error);
        const errorMsg = error.message || "语音转文字失败，请重试";
        message.error(errorMsg);
      } finally {
        setIsConverting(false);
        setIsRecordingModalVisible(false);
        audioChunksRef.current = [];
        setRecordingTime(0);
      }
    };
    
    // 将 WebM 转换为 PCM 格式（参考demo要求：16kHz采样率，raw编码）
    const convertWebmToPcm = async (webmBlob: Blob): Promise<Blob> => {
      try {
        const arrayBuffer = await webmBlob.arrayBuffer();
        const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
        const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
        
        // 目标采样率：16kHz（demo要求）
        const targetSampleRate = 16000;
        const originalSampleRate = audioBuffer.sampleRate;
        const numberOfChannels = audioBuffer.numberOfChannels;
        const duration = audioBuffer.duration;
        
        // 检查音频时长（至少0.5秒，最多60秒）
        if (duration < 0.5) {
          throw new Error("录音时长太短，请至少录制0.5秒");
        }
        if (duration > 60) {
          throw new Error("录音时长过长，请控制在60秒以内");
        }
        
        let pcmData: Float32Array;
        
        // 如果采样率已经是16kHz，直接使用
        if (originalSampleRate === targetSampleRate) {
          pcmData = audioBuffer.getChannelData(0);
        } else {
          // 使用OfflineAudioContext进行高质量重采样
          const offlineContext = new OfflineAudioContext(
            1, // 单声道
            Math.floor(audioBuffer.length * targetSampleRate / originalSampleRate),
            targetSampleRate
          );
          
          const source = offlineContext.createBufferSource();
          source.buffer = audioBuffer;
          source.connect(offlineContext.destination);
          source.start(0);
          
          const resampledBuffer = await offlineContext.startRendering();
          pcmData = resampledBuffer.getChannelData(0);
        }
        
        // 转换为16-bit PCM
        const pcm16 = new Int16Array(pcmData.length);
        for (let i = 0; i < pcmData.length; i++) {
          const s = Math.max(-1, Math.min(1, pcmData[i]));
          pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        
        return new Blob([pcm16.buffer], { type: 'audio/pcm' });
      } catch (error: any) {
        console.error("Error converting to PCM:", error);
        if (error.message && (error.message.includes("太短") || error.message.includes("过长"))) {
          throw error;
        }
        throw new Error("音频格式转换失败，请重试");
      }
    };
    
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
          title={isRecording ? t("正在录音") : isConverting ? t("正在转换") : t("录音")}
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
          closable={!isRecording && !isConverting}
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
                <div className="text-gray-500 text-sm">
                  {t("正在录音中，请说话...")}
                </div>
              </>
            ) : isConverting ? (
              <>
                <div className="mb-4">
                  <div className="animate-spin rounded-full h-16 w-16 border-b-2 border-blue-500"></div>
                </div>
                <div className="text-lg font-semibold mb-2">
                  {t("正在转换语音为文字...")}
                </div>
                <div className="text-gray-500 text-sm">
                  {t("请稍候")}
                </div>
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