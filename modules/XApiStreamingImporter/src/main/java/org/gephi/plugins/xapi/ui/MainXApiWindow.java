package org.gephi.plugins.xapi.ui;

import java.awt.Color;
import java.awt.Component;
import java.awt.Cursor;
import java.awt.Desktop;
import java.awt.Dimension;
import java.awt.GridBagConstraints;
import java.awt.GridBagLayout;
import java.awt.Insets;
import java.awt.event.MouseAdapter;
import java.awt.event.MouseEvent;
import java.net.URI;
import javax.swing.BorderFactory;
import javax.swing.ButtonGroup;
import javax.swing.JButton;
import javax.swing.JEditorPane;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JPasswordField;
import javax.swing.JRadioButton;
import javax.swing.JSpinner;
import javax.swing.JTextField;
import javax.swing.SpinnerNumberModel;
import javax.swing.SwingUtilities;
import javax.swing.UIManager;
import javax.swing.event.HyperlinkEvent;
import org.gephi.plugins.xapi.core.DataPath;
import org.gephi.plugins.xapi.core.SessionConfig;
import org.gephi.plugins.xapi.core.StatusListener;
import org.gephi.plugins.xapi.core.TransportKind;
import org.gephi.plugins.xapi.core.XApiSession;
import org.gephi.plugins.xapi.graph.GraphWriter;
import org.gephi.plugins.xapi.prefs.XApiPreferences;
import org.openide.awt.ActionID;
import org.openide.awt.ActionReference;
import org.openide.windows.TopComponent;
import org.openide.util.NbBundle.Messages;

@TopComponent.Description(preferredID = "MainXApiWindow",
                          persistenceType = TopComponent.PERSISTENCE_NEVER)
@TopComponent.Registration(mode = "explorer", openAtStartup = false)
@ActionID(category = "Window", id = "org.gephi.plugins.xapi.ui.MainXApiWindow")
@ActionReference(path = "Menu/Window", position = 1800)
@TopComponent.OpenActionRegistration(displayName = "#CTL_MainXApiWindow",
                                     preferredID = "MainXApiWindow")
@Messages({
    "CTL_MainXApiWindow=X API Streaming",
    "HINT_MainXApiWindow=Build a user-interaction network from X API v2 searches."
})
public final class MainXApiWindow extends TopComponent {

    private static final long serialVersionUID = 1L;

    private final JTextField keywordField = new JTextField(24);
    private final JPasswordField bearerField = new JPasswordField(24);
    private final JRadioButton transportJavaRadio = new JRadioButton("Java HTTP", true);
    private final JRadioButton transportXurlRadio = new JRadioButton("xurl subprocess");
    private final JRadioButton pollingRadio = new JRadioButton("Polling (search/recent)", true);
    private final JRadioButton streamRadio = new JRadioButton("Filtered stream");
    private final JSpinner intervalSpinner = new JSpinner(new SpinnerNumberModel(30, 15, 3600, 5));
    private final JButton startBtn = new JButton("Start");
    private final JButton stopBtn = new JButton("Stop");
    private final JButton clearBtn = new JButton("Clear graph");
    private final JEditorPane statusPane = new JEditorPane();
    private final JLabel tokenHelpLink = new JLabel(
            "<html><a href=''>How to get a bearer token?</a></html>");

    private static final String TOKEN_HELP_URL =
            "https://developer.x.com/en/portal/dashboard";

    public MainXApiWindow() {
        setName(Bundle.CTL_MainXApiWindow());
        setToolTipText(Bundle.HINT_MainXApiWindow());
        buildLayout();
        wireEvents();
        restorePrefs();
        setStatus(StatusListener.Level.IDLE, "Idle.");
    }

    private void buildLayout() {
        JPanel form = new JPanel(new GridBagLayout());
        form.setBorder(BorderFactory.createEmptyBorder(8, 8, 8, 8));

        GridBagConstraints gc = new GridBagConstraints();
        gc.insets = new Insets(2, 2, 2, 2);
        gc.anchor = GridBagConstraints.WEST;
        gc.fill = GridBagConstraints.HORIZONTAL;

        int row = 0;

        gc.gridx = 0; gc.gridy = row; gc.weightx = 0;
        form.add(new JLabel("Keyword:"), gc);
        gc.gridx = 1; gc.weightx = 1;
        keywordField.setToolTipText("<html>X query syntax supported: <code>nasa -is:retweet lang:en</code></html>");
        form.add(keywordField, gc);
        row++;

        gc.gridx = 0; gc.gridy = row; gc.weightx = 0;
        form.add(new JLabel("Bearer token:"), gc);
        gc.gridx = 1; gc.weightx = 1;
        form.add(bearerField, gc);
        row++;

        gc.gridx = 1; gc.gridy = row;
        tokenHelpLink.setCursor(Cursor.getPredefinedCursor(Cursor.HAND_CURSOR));
        tokenHelpLink.setToolTipText(TOKEN_HELP_URL);
        tokenHelpLink.addMouseListener(new MouseAdapter() {
            @Override
            public void mouseClicked(MouseEvent e) {
                openBrowser(TOKEN_HELP_URL);
            }
        });
        form.add(tokenHelpLink, gc);
        row++;

        gc.gridx = 0; gc.gridy = row;
        form.add(new JLabel("Transport:"), gc);
        gc.gridx = 1;
        JPanel transportGroup = new JPanel();
        transportGroup.setLayout(new javax.swing.BoxLayout(transportGroup, javax.swing.BoxLayout.X_AXIS));
        ButtonGroup bgT = new ButtonGroup();
        bgT.add(transportJavaRadio);
        bgT.add(transportXurlRadio);
        transportGroup.add(transportJavaRadio);
        transportGroup.add(transportXurlRadio);
        transportXurlRadio.setToolTipText("<html>xurl must be on PATH and already authenticated via<br/>"
                + "<code>xurl auth oauth2</code>. Billing tier still applies.</html>");
        form.add(transportGroup, gc);
        row++;

        gc.gridx = 0; gc.gridy = row;
        form.add(new JLabel("Data path:"), gc);
        gc.gridx = 1;
        JPanel pathGroup = new JPanel();
        pathGroup.setLayout(new javax.swing.BoxLayout(pathGroup, javax.swing.BoxLayout.X_AXIS));
        ButtonGroup bgP = new ButtonGroup();
        bgP.add(pollingRadio);
        bgP.add(streamRadio);
        pathGroup.add(pollingRadio);
        pathGroup.add(streamRadio);
        streamRadio.setToolTipText("<html>Long-lived connection with server-side rules.<br/>"
                + "Read endpoint &mdash; consumes X API credits per request.</html>");
        form.add(pathGroup, gc);
        row++;

        gc.gridx = 0; gc.gridy = row;
        form.add(new JLabel("Poll interval (s):"), gc);
        gc.gridx = 1;
        intervalSpinner.setPreferredSize(new Dimension(80, 24));
        form.add(intervalSpinner, gc);
        row++;

        gc.gridx = 0; gc.gridy = row; gc.gridwidth = 2;
        JPanel buttons = new JPanel();
        buttons.setLayout(new javax.swing.BoxLayout(buttons, javax.swing.BoxLayout.X_AXIS));
        buttons.add(startBtn);
        buttons.add(javax.swing.Box.createHorizontalStrut(4));
        buttons.add(stopBtn);
        buttons.add(javax.swing.Box.createHorizontalStrut(4));
        buttons.add(clearBtn);
        buttons.add(javax.swing.Box.createHorizontalGlue());
        form.add(buttons, gc);
        gc.gridwidth = 1;
        row++;

        gc.gridx = 0; gc.gridy = row; gc.gridwidth = 2;
        gc.fill = GridBagConstraints.BOTH;
        gc.weighty = 1;
        statusPane.setContentType("text/html");
        statusPane.setEditable(false);
        statusPane.setOpaque(false);
        statusPane.setBorder(BorderFactory.createEmptyBorder(6, 0, 0, 0));
        statusPane.putClientProperty(JEditorPane.HONOR_DISPLAY_PROPERTIES, Boolean.TRUE);
        statusPane.setFont(UIManager.getFont("Label.font"));
        form.add(statusPane, gc);

        setLayout(new java.awt.BorderLayout());
        add(form, java.awt.BorderLayout.CENTER);

        stopBtn.setEnabled(false);
    }

    private void wireEvents() {
        startBtn.addActionListener(e -> onStart());
        stopBtn.addActionListener(e -> onStop());
        clearBtn.addActionListener(e -> onClear());
        pollingRadio.addActionListener(e -> intervalSpinner.setEnabled(true));
        streamRadio.addActionListener(e -> intervalSpinner.setEnabled(false));
        statusPane.addHyperlinkListener(e -> {
            if (e.getEventType() == HyperlinkEvent.EventType.ACTIVATED) {
                String url = e.getURL() != null ? e.getURL().toString() : e.getDescription();
                openBrowser(url);
            }
        });
    }

    private static void openBrowser(String url) {
        try {
            if (Desktop.isDesktopSupported()
                    && Desktop.getDesktop().isSupported(Desktop.Action.BROWSE)) {
                Desktop.getDesktop().browse(new URI(url));
            }
        } catch (Exception ignored) {
            // No browser available; tooltip still shows the URL.
        }
    }

    private void onStart() {
        String keyword = keywordField.getText().trim();
        if (keyword.isEmpty()) {
            setStatus(StatusListener.Level.ERROR, "Keyword is required.");
            return;
        }
        TransportKind transport = transportXurlRadio.isSelected()
                ? TransportKind.XURL : TransportKind.JAVA_HTTP;
        String token = new String(bearerField.getPassword());
        if (transport == TransportKind.JAVA_HTTP && token.isEmpty()) {
            setStatus(StatusListener.Level.ERROR,
                    "Paste an X API v2 <b>Bearer token</b> above. Get one at "
                    + "<a href='" + TOKEN_HELP_URL + "'>developer.x.com/en/portal/dashboard</a> "
                    + "&rarr; create a Project and App &rarr; Keys and tokens &rarr; Bearer Token.");
            return;
        }
        DataPath dataPath = streamRadio.isSelected() ? DataPath.FILTERED_STREAM : DataPath.POLLING;
        int interval = (Integer) intervalSpinner.getValue();

        SessionConfig cfg = new SessionConfig(keyword, transport, token, dataPath, interval);

        XApiPreferences.setKeyword(keyword);
        XApiPreferences.setInterval(interval);
        XApiPreferences.setTransport(transport.name());
        XApiPreferences.setDataPath(dataPath.name());

        setControlsRunning(true);
        try {
            XApiSession.getDefault().start(cfg, this::onStatusFromWorker);
        } catch (RuntimeException ex) {
            setControlsRunning(false);
            setStatus(StatusListener.Level.ERROR, "Could not start: " + ex.getMessage());
        }
    }

    private void onStop() {
        XApiSession.getDefault().stop();
        setControlsRunning(false);
    }

    private void onClear() {
        try {
            new GraphWriter().clearGraph();
            setStatus(StatusListener.Level.INFO, "Graph cleared.");
        } catch (Exception ex) {
            setStatus(StatusListener.Level.ERROR, "Could not clear graph: " + ex.getMessage());
        }
    }

    private void onStatusFromWorker(StatusListener.Level level, String msg) {
        SwingUtilities.invokeLater(() -> {
            setStatus(level, msg);
            if (level == StatusListener.Level.STOPPED || level == StatusListener.Level.ERROR) {
                setControlsRunning(false);
                // Defensive: make sure the session is fully torn down so the next
                // Start click gets a clean state, even if the worker returned on its own.
                XApiSession.getDefault().stop();
            }
        });
    }

    private void setStatus(StatusListener.Level level, String msg) {
        Color c;
        switch (level) {
            case RUNNING: c = new Color(0, 128, 0); break;
            case INFO:    c = new Color(30, 90, 180); break;
            case WARN:    c = new Color(180, 120, 0); break;
            case ERROR:   c = new Color(180, 30, 30); break;
            case STOPPED: c = Color.DARK_GRAY; break;
            case IDLE:
            default:      c = Color.GRAY;
        }
        String body = msg == null ? "" : msg;
        String hex = String.format("#%02x%02x%02x", c.getRed(), c.getGreen(), c.getBlue());
        java.awt.Font f = UIManager.getFont("Label.font");
        String family = f != null ? f.getFamily() : "Dialog";
        int size = f != null ? f.getSize() : 12;
        String html = "<html><body style=\"font-family:'" + family
                + "';font-size:" + size + "pt;color:" + hex + ";margin:0;padding:0;\">"
                + body + "</body></html>";
        statusPane.setText(html);
        statusPane.setCaretPosition(0);
    }

    private void setControlsRunning(boolean running) {
        startBtn.setEnabled(!running);
        stopBtn.setEnabled(running);
        keywordField.setEnabled(!running);
        bearerField.setEnabled(!running);
        transportJavaRadio.setEnabled(!running);
        transportXurlRadio.setEnabled(!running);
        pollingRadio.setEnabled(!running);
        streamRadio.setEnabled(!running);
        intervalSpinner.setEnabled(!running && pollingRadio.isSelected());
    }

    private void restorePrefs() {
        XApiPreferences.scrubLegacyToken();
        keywordField.setText(XApiPreferences.getKeyword());
        intervalSpinner.setValue(XApiPreferences.getInterval());
        if ("XURL".equals(XApiPreferences.getTransport())) {
            transportXurlRadio.setSelected(true);
        } else {
            transportJavaRadio.setSelected(true);
        }
        if ("FILTERED_STREAM".equals(XApiPreferences.getDataPath())) {
            streamRadio.setSelected(true);
            intervalSpinner.setEnabled(false);
        } else {
            pollingRadio.setSelected(true);
        }
    }

    @Override
    public void componentOpened() {
    }

    @Override
    public void componentClosed() {
        XApiSession.getDefault().stop();
    }
}
