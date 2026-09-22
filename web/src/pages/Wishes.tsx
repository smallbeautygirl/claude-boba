// 許願板。docs/web-spec.md §12。
//
// **這整頁是試玩期的鷹架**，有寫死的下架條件（Phase 2 驗完，或連續 30 天沒有新的
// 一則，先到者為準）。拆的時候是刪掉這個檔案、路由、job 詳情頁那個入口，
// 以及 emoji-picker-react 這個相依（ADR-0004，拆之前先確認沒有別處在用）。
//
// 三件事在改這頁之前要先知道，否則會覺得少了東西：
//
// - **沒有「處理中」。** 只有「實現了」，而且連結必填 —— 沒有連結它就退化成
//   空頭宣告，跟被砍掉的「認領中」變回同一種東西
// - **反應只顯示數字**，不顯示是誰按的。五到十人的團隊裡「他對我的按了 👍，
//   對別人的按了 🎉」是真的會被比較的
// - **不自動帶入任何 job 資訊。** 這頁的入口在 job 詳情頁，那個位置會很自然地
//   誘導出「順手帶上 job id」的設計 —— 那一步就是 SPEC §4.3 的破口
//
// 語氣：這面牆是全站最鬆的一塊，可以玩。**只有一處切正經** —— 貼圖前那句提示
// （web-spec §9 的正經清單第 4 項），而且它放在上傳按鈕旁邊，不是頁面頂端。

import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type Board,
  type Wish,
  type WishCategory,
  type WishComment,
  type WishImageRef,
  type WishReaction,
} from "../api";
// 型別匯入，執行期會被完全抹掉 —— 不會把套件拉進主 bundle。
import type { Theme } from "emoji-picker-react";
import { readTheme } from "../theme";
import "./Wishes.css";

// 不進主 bundle：提交頁與 job 詳情頁的首屏不該為了一個點開才用的選單變胖
// （ADR-0004）。
const EmojiPicker = lazy(() => import("emoji-picker-react"));

// 前端先擋（web-spec §3 的原則：不要讓人傳完才說太大）。後端會**看檔案開頭**
// 再驗一次，因為 content-type 與副檔名都是客戶端自己填的。
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_IMAGES = 3;

// 我們的三態（system / light / dark）對到 picker 的 Theme。值就是這三個字串，
// 用型別匯入避免把它的 enum 拉進主 bundle —— 那會抵銷掉 lazy load。
const PICKER_THEME = {
  system: "auto",
  light: "light",
  dark: "dark",
} as const;

export function Wishes() {
  const [board, setBoard] = useState<Board | null>(null);
  const [filter, setFilter] = useState<WishCategory | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .board(filter ?? undefined)
      .then(setBoard)
      .catch((e: Error) => setError(e.message));
  }, [filter]);

  useEffect(load, [load]);

  if (error) return <div className="card"><p className="error">{error}</p></div>;
  if (!board) return <div className="card">載入中…</div>;

  return (
    <div className="card wishes">
      <h1>許願板 🪄</h1>
      <p className="lede">
        這東西還在試玩。哪裡卡卡的、哪裡想要更多，寫在這裡就好 ——
        看的人是維護者，不用客氣。
      </p>

      <WishComposer categories={board.categories} onDone={load} />

      <div className="chips wish-filters">
        <button
          type="button"
          className={filter === null ? "chip-btn on" : "chip-btn"}
          onClick={() => setFilter(null)}
        >
          全部
        </button>
        {board.categories.map((c) => (
          <button
            key={c.value}
            type="button"
            className={filter === c.value ? "chip-btn on" : "chip-btn"}
            onClick={() => setFilter(c.value)}
          >
            {c.label}
          </button>
        ))}
      </div>

      {board.items.length === 0 && (
        <p className="hint wish-empty">
          {filter
            ? "這一類還沒有人許願。"
            : "一則都還沒有。第一個許願的人可以許三個 —— 騙你的，許幾個都行。"}
        </p>
      )}

      {board.items.map((w) => (
        <WishCard key={w.id} wish={w} categories={board.categories} onDone={load} />
      ))}
    </div>
  );
}

// ── 貼文 ───────────────────────────────────────────────────────────

/* 貼新的與編輯共用這個表單，但**編輯時沒有貼圖欄位** —— 後端的編輯不收
   image_keys（改一個錯字不該把截圖弄丟），留著一個沒有作用的欄位比沒有更糟。
   要換圖就刪掉重貼。 */
function WishComposer({
  categories,
  onDone,
  initial,
  onCancel,
}: {
  categories: Board["categories"];
  onDone: () => void;
  initial?: Wish;
  onCancel?: () => void;
}) {
  const [category, setCategory] = useState<WishCategory>(
    initial?.category ?? "broken",
  );
  const [body, setBody] = useState(initial?.body ?? "");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      if (initial) {
        await api.editWish(initial.id, { category, body });
      } else {
        const image_keys = await Promise.all(files.map((f) => api.uploadWishImage(f)));
        await api.createWish({ category, body, image_keys });
      }
      setBody("");
      setFiles([]);
      onDone();
      onCancel?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="wish-composer">
      {/* 四類的界線是模糊的（「壞掉了」vs「體驗不好」），那是選四類的已知代價。
          補償是貼完可以改 —— 包含分類。 */}
      <div className="chips">
        {categories.map((c) => (
          <button
            key={c.value}
            type="button"
            className={category === c.value ? "chip-btn on" : "chip-btn"}
            onClick={() => setCategory(c.value)}
          >
            {c.label}
          </button>
        ))}
      </div>

      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder="發生了什麼？越具體越好 —— 你在哪一頁、按了什麼、原本以為會怎樣"
        rows={4}
      />

      {initial ? (
        <p className="hint">編輯不動貼圖。要換圖的話刪掉重貼 —— 反應和留言會一起沒有。</p>
      ) : (
        <ImagePicker files={files} onChange={setFiles} />
      )}

      {error && <p className="error">{error}</p>}

      <div className="actions">
        <button disabled={busy || !body.trim()} onClick={submit}>
          {busy ? "送出中…" : initial ? "存起來" : "許願"}
        </button>
        {onCancel && (
          <button className="secondary" onClick={onCancel} disabled={busy}>
            取消
          </button>
        )}
      </div>
    </div>
  );
}

/* 貼圖。web-spec §9 的正經清單第 4 項就是這裡那句話 ——
   它放在**上傳按鈕旁邊**，不是頁面頂端的說明區：頂端的字，人在急著回報 bug
   的時候不會讀（§10 已經為了同一個理由拒絕過獨立的新手說明頁）。 */
function ImagePicker({
  files,
  onChange,
}: {
  files: File[];
  onChange: (next: File[]) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [rejected, setRejected] = useState<string | null>(null);

  function add(picked: FileList | null) {
    if (!picked) return;
    const next = [...files];
    for (const f of Array.from(picked)) {
      if (!IMAGE_TYPES.includes(f.type)) {
        setRejected(`${f.name}：只收 PNG、JPEG、WebP`);
        continue;
      }
      if (f.size > MAX_IMAGE_BYTES) {
        setRejected(`${f.name}：超過 5 MB`);
        continue;
      }
      if (next.length >= MAX_IMAGES) {
        setRejected(`最多 ${MAX_IMAGES} 張`);
        break;
      }
      next.push(f);
    }
    onChange(next);
    if (input.current) input.current.value = "";
  }

  return (
    <div className="wish-images">
      <div className="wish-image-add">
        <input
          ref={input}
          type="file"
          accept={IMAGE_TYPES.join(",")}
          multiple
          onChange={(e) => add(e.target.files)}
        />
        {/* 這句是正經的，不要改成俏皮話。它擋的是「貼一張 job 詳情頁的截圖，
            上面有 prompt、檔名、花費」那件事（ADR-0005）。 */}
        <p className="warn wish-image-warning">
          截圖會被<strong>所有登入的人</strong>看到，而且會一直留著。
          送出前先看一眼上面有沒有你不想給全部人看的東西。
        </p>
      </div>
      {files.length > 0 && (
        <ul className="attach-list">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`}>
              {f.name}
              <button
                className="secondary"
                onClick={() => onChange(files.filter((_, j) => j !== i))}
              >
                拿掉
              </button>
            </li>
          ))}
        </ul>
      )}
      {rejected && <p className="error">{rejected}</p>}
    </div>
  );
}

// ── 一則願望 ───────────────────────────────────────────────────────

function WishCard({
  wish,
  categories,
  onDone,
}: {
  wish: Wish;
  categories: Board["categories"];
  onDone: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [fulfilling, setFulfilling] = useState(false);

  if (editing) {
    return (
      <div className="wish">
        <WishComposer
          categories={categories}
          initial={wish}
          onDone={onDone}
          onCancel={() => setEditing(false)}
        />
      </div>
    );
  }

  return (
    // 實現了的沉到底部、樣式變掉，但**不消失、不收進第二個分頁** ——
    // 已實現的願望是這面牆唯一的活著證據。
    <div className={wish.fulfilled ? "wish done" : "wish"} id={wish.id}>
      <div className="wish-head">
        <span className="chip">{wish.category_label}</span>
        <strong>{wish.author}</strong>
        {/* 不顯示「改過」：web-spec §12 明寫不留編輯歷史也不掛標記。 */}
        <span className="muted">{when(wish.created_at)}</span>
      </div>

      <p className="wish-body">{wish.body}</p>
      <Images images={wish.images} />

      {wish.fulfilled && (
        <p className="wish-done-note">
          🎉 {wish.fulfilled.by} 把它做掉了 ——{" "}
          <a href={wish.fulfilled.link} target="_blank" rel="noreferrer noopener">
            看改了什麼
          </a>
        </p>
      )}

      <Reactions
        target="wish"
        id={wish.id}
        reactions={wish.reactions}
        onDone={onDone}
      />

      <div className="actions wish-actions">
        {/* 任何人都能標，但連結必填。擋住亂標的是那個連結，不是權限 ——
            鷹架、五到十人，加權限模型的成本高於被亂標的成本。 */}
        {!wish.fulfilled && !fulfilling && (
          <button className="secondary" onClick={() => setFulfilling(true)}>
            我做掉了
          </button>
        )}
        {wish.fulfilled && (
          <button
            className="secondary"
            onClick={() => api.unfulfilWish(wish.id).then(onDone)}
          >
            標錯了
          </button>
        )}
        {wish.mine && (
          <>
            <button className="secondary" onClick={() => setEditing(true)}>
              編輯
            </button>
            <button
              className="secondary"
              onClick={() => api.deleteWish(wish.id).then(onDone)}
            >
              刪掉
            </button>
          </>
        )}
      </div>

      {fulfilling && (
        <FulfilForm
          id={wish.id}
          onDone={onDone}
          onCancel={() => setFulfilling(false)}
        />
      )}

      <Comments wish={wish} onDone={onDone} />
    </div>
  );
}

function FulfilForm({
  id,
  onDone,
  onCancel,
}: {
  id: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [link, setLink] = useState("");
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="wish-fulfil">
      <input
        value={link}
        onChange={(e) => setLink(e.target.value)}
        placeholder="PR 或 commit 的連結"
      />
      <button
        disabled={!link.trim()}
        onClick={() =>
          api
            .fulfilWish(id, link)
            .then(onDone)
            .catch((e: Error) => setError(e.message))
        }
      >
        標成實現了
      </button>
      <button className="secondary" onClick={onCancel}>
        取消
      </button>
      {/* 必填不是資料潔癖：沒有連結，「實現了」就退化成空頭宣告。 */}
      <p className="hint">要附連結 —— 不然這則只是「有人說他做了」。</p>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

// ── 留言 ───────────────────────────────────────────────────────────

function Comments({ wish, onDone }: { wish: Wish; onDone: () => void }) {
  const [body, setBody] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const keys = await Promise.all(files.map((f) => api.uploadWishImage(f)));
      await api.addWishComment(wish.id, body, keys);
      setBody("");
      setFiles([]);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="wish-comments">
      {wish.comments.map((c) => (
        <CommentRow key={c.id} comment={c} onDone={onDone} />
      ))}
      <div className="wish-comment-new">
        <input
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="回一句"
          onKeyDown={(e) => {
            if (e.key === "Enter" && body.trim() && !busy) send();
          }}
        />
        <button disabled={busy || !body.trim()} onClick={send}>
          回覆
        </button>
      </div>
      {/* 留言也能貼圖：最有價值的用法就是「你說的是不是長這樣？」的來回。
          不對稱的話人會改用「我再開一則新願望」來繞過，那更糟。 */}
      <ImagePicker files={files} onChange={setFiles} />
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function CommentRow({
  comment,
  onDone,
}: {
  comment: WishComment;
  onDone: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(comment.body);

  if (editing) {
    return (
      <div className="wish-comment">
        <div className="wish-comment-new">
          <input value={draft} onChange={(e) => setDraft(e.target.value)} />
          <button
            disabled={!draft.trim()}
            onClick={() =>
              api.editWishComment(comment.id, draft).then(() => {
                setEditing(false);
                onDone();
              })
            }
          >
            存起來
          </button>
          <button className="secondary" onClick={() => setEditing(false)}>
            取消
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="wish-comment">
      <div className="wish-head">
        <strong>{comment.author}</strong>
        <span className="muted">{when(comment.created_at)}</span>
      </div>
      <p>{comment.body}</p>
      <Images images={comment.images} />
      <Reactions
        target="comment"
        id={comment.id}
        reactions={comment.reactions}
        onDone={onDone}
      />
      {comment.mine && (
        <div className="actions">
          <button className="secondary" onClick={() => setEditing(true)}>
            編輯
          </button>
          <button
            className="secondary"
            onClick={() => api.deleteWishComment(comment.id).then(onDone)}
          >
            刪掉
          </button>
        </div>
      )}
    </div>
  );
}

// ── 反應 ───────────────────────────────────────────────────────────

function Reactions({
  target,
  id,
  reactions,
  onDone,
}: {
  target: "wish" | "comment";
  id: string;
  reactions: WishReaction[];
  onDone: () => void;
}) {
  const [picking, setPicking] = useState(false);

  function react(emoji: string) {
    setPicking(false);
    api.reactToWish(target, id, emoji).then(onDone);
  }

  return (
    <div className="wish-reactions">
      {/* 只有數字。**不顯示是誰按的**，也沒有 hover 名單 —— 那跟排行榜
          只顯示聚合數字是同一條線（web-spec §7、§12）。 */}
      {reactions.map((r) => (
        <button
          key={r.emoji}
          type="button"
          className={r.mine ? "chip-btn on" : "chip-btn"}
          onClick={() => react(r.emoji)}
        >
          {r.emoji} {r.count}
        </button>
      ))}
      <button
        type="button"
        className="chip-btn"
        onClick={() => setPicking((p) => !p)}
        aria-label="加一個反應"
      >
        ＋
      </button>
      {picking && (
        <div className="wish-picker">
          <Suspense fallback={<span className="hint">選單載入中…</span>}>
            <EmojiPicker
              onEmojiClick={(e: { emoji: string }) => react(e.emoji)}
              lazyLoadEmojis
              /* 關掉膚色選擇器。它長得突兀只是表面理由，真正的理由是**它會把
                 同一顆 emoji 拆成好幾筆反應** —— 👍 和 👍🏽 是不同的字串，
                 所以會各自算一格。牆上只顯示數字（web-spec §12），而五到十個人
                 的規模下，三個人按了「同一顆」卻顯示成三個 1，那個數字就沒用了。 */
              skinTonesDisabled
              /* picker 有自己的亮暗，不會跟著我們的 data-theme 走 ——
                 不給的話，暗色模式下它是一塊白的。這裡讀的是當下的值而不是用
                 useTheme()：picker 只在打開的那一刻才存在，而 useTheme 的 effect
                 會再寫一次 document 的屬性，那不該由一個彈出選單來做。 */
              theme={PICKER_THEME[readTheme()] as Theme}
              /* 底下那條「What's Your Mood?」預覽列關掉。它是給「挑一顆 emoji
                 插進文字」那種情境用的 —— 這裡挑完就直接送出，預覽的那一顆
                 從來不會被看第二眼，只占高度。 */
              previewConfig={{ showPreview: false }}
              /* 壓小。預設尺寸會蓋掉下面一整則願望，而這個選單是**暫時**的
                 東西：它擋住的內容正是你按完之後要回去看的。 */
              width={300}
              height={360}
            />
          </Suspense>
        </div>
      )}
    </div>
  );
}

// ── 貼圖的顯示 ─────────────────────────────────────────────────────

function Images({ images }: { images: WishImageRef[] }) {
  if (images.length === 0) return null;
  return (
    <div className="wish-shots">
      {images.map((i) => (
        <AuthedImage key={i.id} url={i.url} />
      ))}
    </div>
  );
}

/* 圖片要帶 Authorization，所以 <img src> 直接指過去會拿到 401 ——
   後端刻意不給預簽 URL：一張帶著 prompt 的截圖若有不用登入就打得開的位址，
   外洩得比這面牆本身更遠（ADR-0005）。抓成 blob 再給 object URL，
   離開時 revoke，否則每次重新整理都漏一份在記憶體裡。 */
function AuthedImage({ url }: { url: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let revoked = false;
    let objectUrl: string | null = null;
    api
      .fetchWishImage(url)
      .then((u) => {
        objectUrl = u;
        if (revoked) URL.revokeObjectURL(u);
        else setSrc(u);
      })
      .catch(() => setFailed(true));
    return () => {
      revoked = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);

  if (failed) return <span className="hint">（這張圖載不到了）</span>;
  if (!src) return <span className="hint">圖片載入中…</span>;
  return <img className="wish-shot" src={src} alt="" />;
}

function when(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days === 0) return "今天";
  if (days === 1) return "昨天";
  return `${days} 天前`;
}
