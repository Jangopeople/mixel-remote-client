using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using MixelRemote.App.Shell;
using MixelRemote.Models;
using MixelRemote.ViewModels;

namespace MixelRemote.App.Session;

/// <summary>Chat, files, clipboard (with typed text and print jobs) and AI help during a session.</summary>
internal sealed class ToolPanel : PageView
{
    public ToolPanel(AppModel app, RemoteScreen screen) : base(app)
    {
        Watch(app.Assistant);
        app.Assistant.Screenshot = () => screen.Snapshot();
    }

    /// <summary>chat, files, clipboard or help.</summary>
    public string Tool { get; set; } = "chat";

    protected override UIElement Build()
    {
        Background = Ui.Brush("LayerFillColorDefaultBrush");
        BorderBrush = Ui.Brush("DividerStrokeColorDefaultBrush");
        BorderThickness = new Thickness(1, 0, 0, 0);
        var (title, body) = Tool switch
        {
            "files" => (L("Files"), Files()),
            "clipboard" => (L("Clipboard"), Clipboard()),
            "help" => (L("AI help"), Help()),
            _ => (L("Chat"), Chat()),
        };
        var stack = Ui.Column(12, Ui.Heading(title), body);
        stack.Padding = new Thickness(16);
        return new ScrollViewer { Content = stack };
    }

    private StackPanel Chat()
    {
        var session = App.Session;
        var messages = Ui.Column(6);
        foreach (var message in session.Chat)
        {
            var bubble = new Border
            {
                Padding = new Thickness(10, 7, 10, 7),
                CornerRadius = new CornerRadius(10),
                MaxWidth = 240,
                HorizontalAlignment = message.Mine ? HorizontalAlignment.Right : HorizontalAlignment.Left,
                Background = message.Mine ? Ui.BrandBrush : Ui.Brush("CardBackgroundFillColorDefaultBrush"),
                Child = new TextBlock { Text = message.Text, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true, Foreground = message.Mine ? new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.White) : Ui.Brush("TextFillColorPrimaryBrush") },
            };
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(bubble, (message.Mine ? L("You") : L("Other side")) + ": " + message.Text);
            messages.Children.Add(bubble);
        }
        if (session.Chat.Count == 0) messages.Children.Add(Ui.Caption(L("Messages appear here.")));
        var draft = Keep("chat", () =>
        {
            var box = new TextBox { PlaceholderText = L("Chat message"), MinWidth = 190 };
            box.TextChanged += (_, _) => App.Session.ChatDraft = box.Text;
            box.KeyDown += (sender, key) =>
            {
                if (key.Key != Windows.System.VirtualKey.Enter) return;
                _ = App.Session.SendChat();
                box.Text = "";
            };
            return box;
        });
        return Ui.Column(10, messages, Ui.Row(8, draft, Ui.Plain(L("Send"), () => { _ = session.SendChat(); draft.Text = ""; })));
    }

    private StackPanel Files()
    {
        var remote = App.Remote;
        var session = App.Session;
        if (!remote.Files) return Ui.Column(8, Ui.Text(L("File transfer was not allowed for this session."), secondary: true));
        var view = session.Files;
        var busyOutgoing = view?.Outgoing is { Phase: "offered" or "waiting" or "transferring" or "verifying" or "paused" };
        var items = Ui.Column(10, Ui.Plain(L("Send a file…"), async () =>
        {
            if (await App.ChooseFile() is { } path) await session.SendFile(path);
        }, view?.Connected == true && !busyOutgoing, "\uE898"));
        if (view?.Outgoing is { } outgoing) items.Children.Add(Transfer(outgoing, true));
        if (view?.Incoming is { } incoming)
        {
            items.Children.Add(Transfer(incoming, false));
            if (incoming.Phase == "offered")
            {
                items.Children.Add(Ui.Row(8,
                    Ui.Primary(L("Accept and save as…"), async () =>
                    {
                        var name = await session.OfferedName(incoming.Id) ?? incoming.Name;
                        if (await App.ChooseSaveLocation(name) is { } path) await session.AcceptFile(incoming.Id, path);
                    }),
                    Ui.Plain(L("Decline"), () => _ = session.CancelFile(incoming.Id))));
            }
        }
        if (view is { Message.Length: > 0 }) items.Children.Add(Ui.Caption(Translator.Shared.Translate(view.Message)));
        return items;
    }

    private Border Transfer(Transfer transfer, bool outgoing)
    {
        var cancel = transfer.Phase is "transferring" or "waiting" or "paused" or "offered"
            ? Ui.Plain(L("Cancel"), () => _ = App.Session.CancelFile(transfer.Id))
            : null;
        return new Border
        {
            Padding = new Thickness(10),
            CornerRadius = new CornerRadius(8),
            Background = Ui.Brush("CardBackgroundFillColorDefaultBrush"),
            Child = Ui.Column(6,
                Ui.Row(8, Ui.Icon(outgoing ? "\uE898" : "\uE896", 14), Ui.Text(transfer.Name, wrap: false)),
                new ProgressBar { Value = transfer.Progress * 100, Maximum = 100 },
                Ui.Split(Ui.Caption($"{Format.Bytes(transfer.Offset)} / {Format.Bytes(transfer.Size)}"), cancel),
                transfer.Message.Length > 0 ? Ui.Caption(Translator.Shared.Translate(transfer.Message)) : null),
        };
    }

    private StackPanel Clipboard()
    {
        var remote = App.Remote;
        var session = App.Session;
        var items = Ui.Column(10);
        if (remote.Clipboard)
        {
            items.Children.Add(Ui.Plain(L("Send my clipboard"), () => _ = session.Clipboard(push: true), glyph: "\uE77F"));
            items.Children.Add(Ui.Plain(L("Get remote clipboard"), () => _ = session.Clipboard(push: false), glyph: "\uE8C8"));
            if (Ui.Message(session.ClipboardResult, error: false) is { } result) items.Children.Add(result);
        }
        else
        {
            items.Children.Add(Ui.Text(L("The clipboard was not allowed for this session."), secondary: true));
        }
        if (remote.Control)
        {
            var text = Keep("type", () =>
            {
                var box = new TextBox { PlaceholderText = L("Text to type on the remote computer"), AcceptsReturn = false };
                box.TextChanged += (_, _) => App.Session.TextToSend = box.Text;
                box.KeyDown += (sender, key) =>
                {
                    if (key.Key != Windows.System.VirtualKey.Enter) return;
                    App.Session.TypeText();
                    box.Text = "";
                };
                return box;
            });
            items.Children.Add(Ui.Heading(L("Send text")));
            items.Children.Add(text);
            items.Children.Add(Ui.Plain(L("Send"), () => { session.TypeText(); text.Text = ""; }));
        }
        if (remote.PrintJobs.Count > 0)
        {
            items.Children.Add(Ui.Heading(L("Print jobs")));
            foreach (var path in remote.PrintJobs)
            {
                items.Children.Add(Ui.Split(Ui.Text(Path.GetFileName(path), wrap: false),
                    Ui.Row(6, Ui.Plain(L("Print"), () => _ = session.PrintJob(path, false)), Ui.Plain(L("Open"), () => _ = session.PrintJob(path, true)))));
            }
        }
        return items;
    }

    private StackPanel Help()
    {
        var assistant = App.Assistant;
        var items = Ui.Column(10);
        if (!assistant.Connected)
        {
            var address = Keep("ai-address", () =>
            {
                var box = new TextBox { PlaceholderText = "http://ai.company.local:11434", Text = assistant.Address };
                box.TextChanged += (_, _) => App.Assistant.Address = box.Text;
                return box;
            });
            items.Children.Add(Ui.Text(L("Your company's own AI server explains the remote screen, suggests the next steps and writes the ticket note."), secondary: true));
            items.Children.Add(Ui.Field(L("AI server address"), address));
            items.Children.Add(Ui.Plain(L("Connect"), () => _ = assistant.Connect(announce: true)));
            if (assistant.Status.Length > 0) items.Children.Add(Ui.Caption(assistant.Status));
            if (assistant.Models.Count == 0 && assistant.Address.Length == 0) _ = assistant.Connect(announce: false);
            return items;
        }
        if (assistant.Models.Count > 1)
        {
            items.Children.Add(Ui.Field(L("Model"), Ui.Choice(assistant.Models.Select(model => (model, model)), assistant.Model, value => { assistant.Model = value ?? assistant.Model; _ = assistant.Connect(announce: false); })));
        }
        var question = Keep("ai-question", () =>
        {
            var box = new TextBox { PlaceholderText = L("Question, or keywords for the note") };
            box.TextChanged += (_, _) => App.Assistant.Question = box.Text;
            return box;
        });
        items.Children.Add(question);
        items.Children.Add(Ui.Row(8,
            Ui.Primary(L("Explain the screen"), () => _ = assistant.Ask(), !assistant.Busy),
            Ui.Plain(L("Write the note"), () => _ = assistant.WriteNote(), !assistant.Busy)));
        var answer = Keep("ai-answer", () =>
        {
            var box = new TextBox { AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 160 };
            box.TextChanged += (_, _) => { if (box.FocusState != FocusState.Unfocused) App.Assistant.Answer = box.Text; };
            return box;
        });
        if (answer.FocusState == FocusState.Unfocused && answer.Text != assistant.Answer) answer.Text = assistant.Answer;
        items.Children.Add(answer);
        if (assistant.Answer.Length > 0) items.Children.Add(Ui.Plain(L("Copy"), () => App.CopyText(assistant.Answer), glyph: "\uE8C8"));
        if (App.Account.Account?.Staff == true)
        {
            var ticket = Keep("ai-ticket", () =>
            {
                var box = new TextBox { PlaceholderText = L("Ticket number"), MinWidth = 140 };
                box.TextChanged += (_, _) => App.Assistant.Ticket = box.Text;
                return box;
            });
            items.Children.Add(Ui.Heading(L("Save in an ISM ticket")));
            items.Children.Add(Ui.Row(8, ticket, Ui.Plain(L("Save"), () => _ = assistant.SaveInTicket())));
            if (assistant.TicketStatus.Length > 0) items.Children.Add(Ui.Caption(assistant.TicketStatus));
        }
        return items;
    }
}
